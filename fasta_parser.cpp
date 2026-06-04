/*
 * High-performance FASTA/FASTQ Parser for AWS Lambda
 * Compiles to binary for use in Lambda Layer
 * 
 * Compile: g++ -O3 -std=c++17 fasta_parser.cpp -o fasta_parser
 */

#include <iostream>
#include <fstream>
#include <string>
#include <vector>
#include <sstream>
#include <algorithm>
#include <unordered_map>
#include <cctype>
#include <nlohmann/json.hpp>

using json = nlohmann::json;

// Structure to hold sequence information
struct SequenceRecord {
    std::string id;
    std::string description;
    std::string sequence;
    std::string quality;  // For FASTQ files
    size_t length;
    double gc_content;
    
    // Calculate GC content
    void calculateGCContent() {
        if (sequence.empty()) {
            gc_content = 0.0;
            return;
        }
        
        size_t gc_count = 0;
        for (char base : sequence) {
            char upper_base = std::toupper(base);
            if (upper_base == 'G' || upper_base == 'C') {
                gc_count++;
            }
        }
        gc_content = (static_cast<double>(gc_count) / sequence.length()) * 100.0;
    }
    
    // Get base composition
    std::unordered_map<char, int> getBaseComposition() {
        std::unordered_map<char, int> composition;
        composition['A'] = 0;
        composition['T'] = 0;
        composition['G'] = 0;
        composition['C'] = 0;
        composition['N'] = 0;
        
        for (char base : sequence) {
            char upper_base = std::toupper(base);
            if (composition.find(upper_base) != composition.end()) {
                composition[upper_base]++;
            } else {
                composition['N']++;
            }
        }
        return composition;
    }
};

struct PatternHit {
    std::string sequence_id;
    std::string pattern_type;
    std::string pattern_name;
    size_t start;
    size_t end;
    size_t length;
    std::string strand;
    double score;
    std::string matched_sequence;
};

struct RegionSummary {
    std::string sequence_id;
    std::string region_type;
    size_t window_start;
    size_t window_end;
    size_t length;
    double gc_content;
    double n_content;
    double gc_skew;
    size_t motif_hits;
    size_t orf_count;
    size_t repeat_bases;
    size_t max_homopolymer_run;
};

std::string normalizeSequence(const std::string& sequence) {
    std::string normalized = sequence;
    std::transform(normalized.begin(), normalized.end(), normalized.begin(),
                   [](unsigned char c) { return static_cast<char>(std::toupper(c)); });
    return normalized;
}

std::vector<PatternHit> detectMotifs(
    const SequenceRecord& record,
    const std::vector<std::pair<std::string, std::string>>& motifs
) {
    std::vector<PatternHit> hits;
    const std::string sequence = normalizeSequence(record.sequence);

    for (const auto& motif : motifs) {
        const std::string& motif_name = motif.first;
        const std::string& motif_sequence = motif.second;

        size_t pos = sequence.find(motif_sequence);
        while (pos != std::string::npos) {
            hits.push_back({
                record.id,
                "motif",
                motif_name,
                pos,
                pos + motif_sequence.length(),
                motif_sequence.length(),
                "+",
                static_cast<double>(motif_sequence.length()),
                motif_sequence
            });
            pos = sequence.find(motif_sequence, pos + 1);
        }
    }

    return hits;
}

std::vector<PatternHit> detectHomopolymers(const SequenceRecord& record, size_t min_run = 6) {
    std::vector<PatternHit> hits;
    const std::string sequence = normalizeSequence(record.sequence);
    if (sequence.empty()) {
        return hits;
    }

    size_t run_start = 0;
    for (size_t i = 1; i <= sequence.length(); ++i) {
        const bool run_continues = i < sequence.length() && sequence[i] == sequence[run_start];
        if (run_continues) {
            continue;
        }

        size_t run_length = i - run_start;
        if (run_length >= min_run) {
            hits.push_back({
                record.id,
                "repeat",
                std::string("homopolymer_") + sequence[run_start],
                run_start,
                i,
                run_length,
                "+",
                static_cast<double>(run_length),
                sequence.substr(run_start, run_length)
            });
        }
        run_start = i;
    }

    return hits;
}

bool isStopCodon(const std::string& codon) {
    return codon == "TAA" || codon == "TAG" || codon == "TGA";
}

std::vector<PatternHit> detectORFs(const SequenceRecord& record, size_t min_orf_length = 90) {
    std::vector<PatternHit> hits;
    const std::string sequence = normalizeSequence(record.sequence);

    for (size_t frame = 0; frame < 3; ++frame) {
        size_t i = frame;
        while (i + 3 <= sequence.length()) {
            if (sequence.compare(i, 3, "ATG") != 0) {
                i += 3;
                continue;
            }

            size_t j = i + 3;
            bool found_stop = false;
            while (j + 3 <= sequence.length()) {
                std::string codon = sequence.substr(j, 3);
                if (isStopCodon(codon)) {
                    size_t orf_length = (j + 3) - i;
                    if (orf_length >= min_orf_length) {
                        hits.push_back({
                            record.id,
                            "orf",
                            std::string("candidate_orf_frame_") + std::to_string(frame),
                            i,
                            j + 3,
                            orf_length,
                            "+",
                            static_cast<double>(orf_length),
                            sequence.substr(i, std::min<size_t>(orf_length, 30))
                        });
                    }
                    found_stop = true;
                    break;
                }
                j += 3;
            }

            i = found_stop ? j + 3 : i + 3;
        }
    }

    return hits;
}

size_t longestHomopolymerRun(const std::string& sequence) {
    if (sequence.empty()) {
        return 0;
    }

    size_t max_run = 1;
    size_t current_run = 1;
    for (size_t i = 1; i < sequence.length(); ++i) {
        if (sequence[i] == sequence[i - 1]) {
            current_run++;
            max_run = std::max(max_run, current_run);
        } else {
            current_run = 1;
        }
    }
    return max_run;
}

std::vector<RegionSummary> summarizeRegions(
    const SequenceRecord& record,
    const std::vector<PatternHit>& pattern_hits,
    size_t window_size = 100000,
    size_t window_step = 50000
) {
    std::vector<RegionSummary> summaries;
    const std::string sequence = normalizeSequence(record.sequence);
    if (sequence.empty()) {
        return summaries;
    }

    const size_t effective_window = std::min(window_size, sequence.length());
    const size_t effective_step = std::min(window_step, effective_window);

    for (size_t start = 0; start < sequence.length(); start += effective_step) {
        size_t end = std::min(start + effective_window, sequence.length());
        const std::string window = sequence.substr(start, end - start);
        if (window.empty()) {
            continue;
        }

        size_t gc_bases = 0;
        size_t n_bases = 0;
        size_t g_bases = 0;
        size_t c_bases = 0;

        for (char base : window) {
            if (base == 'G' || base == 'C') {
                gc_bases++;
            }
            if (base == 'G') {
                g_bases++;
            }
            if (base == 'C') {
                c_bases++;
            }
            if (base == 'N') {
                n_bases++;
            }
        }

        size_t motif_hits = 0;
        size_t orf_count = 0;
        size_t repeat_bases = 0;
        for (const auto& hit : pattern_hits) {
            if (hit.end <= start || hit.start >= end) {
                continue;
            }

            if (hit.pattern_type == "motif") {
                motif_hits++;
            } else if (hit.pattern_type == "orf") {
                orf_count++;
            } else if (hit.pattern_type == "repeat") {
                const size_t overlap_start = std::max(start, hit.start);
                const size_t overlap_end = std::min(end, hit.end);
                repeat_bases += (overlap_end > overlap_start) ? (overlap_end - overlap_start) : 0;
            }
        }

        const double length = static_cast<double>(window.length());
        const double gc_skew_denominator = static_cast<double>(g_bases + c_bases);
        const double gc_skew = gc_skew_denominator == 0.0
            ? 0.0
            : static_cast<double>(g_bases - c_bases) / gc_skew_denominator;

        summaries.push_back({
            record.id,
            "window",
            start,
            end,
            window.length(),
            (static_cast<double>(gc_bases) / length) * 100.0,
            (static_cast<double>(n_bases) / length) * 100.0,
            gc_skew,
            motif_hits,
            orf_count,
            repeat_bases,
            longestHomopolymerRun(window)
        });

        if (end == sequence.length()) {
            break;
        }
    }

    return summaries;
}

// FASTA Parser
class FASTAParser {
private:
    std::ifstream file;
    
public:
    FASTAParser(const std::string& filename) {
        file.open(filename);
        if (!file.is_open()) {
            throw std::runtime_error("Cannot open file: " + filename);
        }
    }
    
    ~FASTAParser() {
        if (file.is_open()) {
            file.close();
        }
    }
    
    std::vector<SequenceRecord> parseAll() {
        std::vector<SequenceRecord> records;
        std::string line;
        SequenceRecord current_record;
        bool in_sequence = false;
        
        while (std::getline(file, line)) {
            // Remove trailing whitespace
            line.erase(line.find_last_not_of(" \n\r\t") + 1);
            
            if (line.empty()) continue;
            
            if (line[0] == '>') {
                // Save previous record if exists
                if (in_sequence) {
                    current_record.length = current_record.sequence.length();
                    current_record.calculateGCContent();
                    records.push_back(current_record);
                }
                
                // Start new record
                current_record = SequenceRecord();
                std::string header = line.substr(1);
                
                // Split ID and description
                size_t space_pos = header.find(' ');
                if (space_pos != std::string::npos) {
                    current_record.id = header.substr(0, space_pos);
                    current_record.description = header.substr(space_pos + 1);
                } else {
                    current_record.id = header;
                    current_record.description = "";
                }
                
                in_sequence = true;
            } else if (in_sequence) {
                // Append to sequence
                current_record.sequence += line;
            }
        }
        
        // Save last record
        if (in_sequence) {
            current_record.length = current_record.sequence.length();
            current_record.calculateGCContent();
            records.push_back(current_record);
        }
        
        return records;
    }
};

// FASTQ Parser
class FASTQParser {
private:
    std::ifstream file;
    
public:
    FASTQParser(const std::string& filename) {
        file.open(filename);
        if (!file.is_open()) {
            throw std::runtime_error("Cannot open file: " + filename);
        }
    }
    
    ~FASTQParser() {
        if (file.is_open()) {
            file.close();
        }
    }
    
    std::vector<SequenceRecord> parseAll() {
        std::vector<SequenceRecord> records;
        std::string line;
        
        while (std::getline(file, line)) {
            SequenceRecord record;
            
            // Line 1: @sequence_id
            if (line.empty() || line[0] != '@') continue;
            
            std::string header = line.substr(1);
            size_t space_pos = header.find(' ');
            if (space_pos != std::string::npos) {
                record.id = header.substr(0, space_pos);
                record.description = header.substr(space_pos + 1);
            } else {
                record.id = header;
                record.description = "";
            }
            
            // Line 2: sequence
            if (!std::getline(file, line)) break;
            line.erase(line.find_last_not_of(" \n\r\t") + 1);
            record.sequence = line;
            
            // Line 3: + (separator)
            if (!std::getline(file, line)) break;
            
            // Line 4: quality scores
            if (!std::getline(file, line)) break;
            line.erase(line.find_last_not_of(" \n\r\t") + 1);
            record.quality = line;
            
            record.length = record.sequence.length();
            record.calculateGCContent();
            records.push_back(record);
        }
        
        return records;
    }
};

// Detect file format
std::string detectFormat(const std::string& filename) {
    std::ifstream file(filename);
    if (!file.is_open()) {
        throw std::runtime_error("Cannot open file: " + filename);
    }
    
    std::string first_line;
    std::getline(file, first_line);
    file.close();
    
    if (!first_line.empty()) {
        if (first_line[0] == '>') return "FASTA";
        if (first_line[0] == '@') return "FASTQ";
    }
    
    throw std::runtime_error("Unknown file format");
}

// Convert records to JSON
json recordsToJSON(
    const std::vector<SequenceRecord>& records,
    bool include_sequence = true,
    bool include_analysis = true
) {
    json output;
    output["format"] = "genome_sequences";
    output["record_count"] = records.size();
    
    json sequences = json::array();
    
    for (const auto& record : records) {
        json seq;
        seq["id"] = record.id;
        seq["description"] = record.description;
        seq["length"] = record.length;
        seq["gc_content"] = record.gc_content;
        
        if (include_sequence) {
            seq["sequence"] = record.sequence;
        } else {
            seq["sequence"] = nullptr;
        }
        
        if (!record.quality.empty()) {
            seq["quality"] = include_sequence ? json(record.quality) : json(nullptr);
        }
        
        // Add base composition
        auto composition = const_cast<SequenceRecord&>(record).getBaseComposition();
        seq["base_composition"]["A"] = composition['A'];
        seq["base_composition"]["T"] = composition['T'];
        seq["base_composition"]["G"] = composition['G'];
        seq["base_composition"]["C"] = composition['C'];
        seq["base_composition"]["N"] = composition['N'];
        
        sequences.push_back(seq);
    }
    
    json patterns = json::array();
    json regions = json::array();

    if (!include_analysis) {
        output["sequences"] = sequences;
        output["patterns"] = patterns;
        output["regions"] = regions;
        return output;
    }

    const std::vector<std::pair<std::string, std::string>> motifs = {
        {"start_codon", "ATG"},
        {"tata_box", "TATAAA"},
        {"polyadenylation_signal", "AATAAA"},
        {"cpg_hotspot", "CGCG"},
        {"gc_rich_box", "GGGCGG"},
        {"gata_motif", "GATA"}
    };

    for (const auto& record : records) {
        std::vector<PatternHit> record_patterns = detectMotifs(record, motifs);
        std::vector<PatternHit> homopolymers = detectHomopolymers(record);
        std::vector<PatternHit> orfs = detectORFs(record);

        record_patterns.insert(record_patterns.end(), homopolymers.begin(), homopolymers.end());
        record_patterns.insert(record_patterns.end(), orfs.begin(), orfs.end());

        for (const auto& hit : record_patterns) {
            json pattern;
            pattern["sequence_id"] = hit.sequence_id;
            pattern["pattern_type"] = hit.pattern_type;
            pattern["pattern_name"] = hit.pattern_name;
            pattern["start"] = hit.start;
            pattern["end"] = hit.end;
            pattern["length"] = hit.length;
            pattern["strand"] = hit.strand;
            pattern["score"] = hit.score;
            pattern["matched_sequence"] = hit.matched_sequence;
            patterns.push_back(pattern);
        }

        std::vector<RegionSummary> record_regions = summarizeRegions(record, record_patterns);
        for (const auto& region : record_regions) {
            json summary;
            summary["sequence_id"] = region.sequence_id;
            summary["region_type"] = region.region_type;
            summary["window_start"] = region.window_start;
            summary["window_end"] = region.window_end;
            summary["length"] = region.length;
            summary["gc_content"] = region.gc_content;
            summary["n_content"] = region.n_content;
            summary["gc_skew"] = region.gc_skew;
            summary["motif_hits"] = region.motif_hits;
            summary["orf_count"] = region.orf_count;
            summary["repeat_bases"] = region.repeat_bases;
            summary["max_homopolymer_run"] = region.max_homopolymer_run;
            regions.push_back(summary);
        }
    }

    output["sequences"] = sequences;
    output["patterns"] = patterns;
    output["regions"] = regions;
    return output;
}

int main(int argc, char* argv[]) {
    if (argc < 3) {
        std::cerr << "Usage: " << argv[0] << " <input_file> <output_json> [full|sequence_only]" << std::endl;
        return 1;
    }
    
    std::string input_file = argv[1];
    std::string output_file = argv[2];
    std::string analysis_mode = argc >= 4 ? argv[3] : "full";
    
    try {
        std::cout << "Parsing file: " << input_file << std::endl;
        
        // Detect format
        std::string format = detectFormat(input_file);
        std::cout << "Detected format: " << format << std::endl;
        
        std::vector<SequenceRecord> records;
        
        if (format == "FASTA") {
            FASTAParser parser(input_file);
            records = parser.parseAll();
        } else if (format == "FASTQ") {
            FASTQParser parser(input_file);
            records = parser.parseAll();
        }
        
        std::cout << "Parsed " << records.size() << " sequences" << std::endl;
        
        if (analysis_mode != "full" && analysis_mode != "sequence_only") {
            throw std::runtime_error("Unsupported analysis mode: " + analysis_mode);
        }

        const bool include_sequence = analysis_mode == "full";
        const bool include_analysis = analysis_mode == "full";

        // Convert to JSON
        json output = recordsToJSON(records, include_sequence, include_analysis);
        
        // Write to file
        std::ofstream out(output_file);
        if (!out.is_open()) {
            throw std::runtime_error("Cannot create output file: " + output_file);
        }
        
        out << output.dump();
        out.close();
        
        std::cout << "Output written to: " << output_file << std::endl;
        return 0;
        
    } catch (const std::exception& e) {
        std::cerr << "Error: " << e.what() << std::endl;
        return 1;
    }
}
