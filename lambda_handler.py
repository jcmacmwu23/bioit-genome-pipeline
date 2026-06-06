"""
AWS Lambda Handler for Genome Sequence Data Pipeline
Pulls FASTA/FASTQ files, processes with C++ parser, converts to Parquet
"""
import json
import boto3
import os
import subprocess
from typing import Dict, List, Any, Optional
import tempfile
import logging
import re
from datetime import datetime
from operations_store import record_failure, record_start, record_success

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# AWS clients
s3_client = boto3.client('s3')
sqs_client = boto3.client('sqs')
glue_client = boto3.client('glue')
athena_client = boto3.client('athena')

# Environment variables
OUTPUT_BUCKET = os.environ.get('OUTPUT_BUCKET')
CPP_PARSER_PATH = '/opt/bin/fasta_parser'  # C++ binary in Lambda layer
DATASET_PREFIXES = {
    'sequences': 'genome_data',
    'patterns': 'pattern_data',
    'regions': 'region_data',
    'annotations': 'gene_annotation_data',
    'raw': 'raw_data',
}
ALLOWED_ANALYSIS_MODES = {'full', 'sequence_only'}
ALLOWED_JOB_TYPES = {'sequence_analysis', 'gene_annotations'}
HUMAN_CHROMOSOME_LENGTHS = {
    '1': 248956422,
    '2': 242193529,
    '3': 198295559,
    '4': 190214555,
    '5': 181538259,
    '6': 170805979,
    '7': 159345973,
    '8': 145138636,
    '9': 138394717,
    '10': 133797422,
    '11': 135086622,
    '12': 133275309,
    '13': 114364328,
    '14': 107043718,
    '15': 101991189,
    '16': 90338345,
    '17': 83257441,
    '18': 80373285,
    '19': 58617616,
    '20': 64444167,
    '21': 46709983,
    '22': 50818468,
    'X': 156040895,
    'Y': 57227415,
}
ENSEMBL_GENE_CHUNK_SIZE = 5_000_000
GLUE_DATABASE = os.environ.get('GLUE_DATABASE', 'genome_pipeline_db')
ATHENA_DATABASE = os.environ.get('ATHENA_DATABASE', GLUE_DATABASE)
ATHENA_WORKGROUP = os.environ.get('ATHENA_WORKGROUP')
ATHENA_RESULTS_BUCKET = os.environ.get('ATHENA_RESULTS_BUCKET')
GLUE_TABLES = {
    'sequences': 'genome_sequences',
    'patterns': 'sequence_patterns',
    'regions': 'sequence_regions',
    'annotations': 'gene_annotations',
}
_GLUE_TABLE_CACHE: Dict[str, Dict[str, Any]] = {}


def extract_job_events(event: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Normalize direct invokes and SQS-triggered events into job payloads.
    """
    records = event.get('Records')
    if not records:
        return [event]

    jobs = []
    for record in records:
        body = record.get('body')
        if body is None:
            jobs.append(record)
            continue
        jobs.append(json.loads(body))
    return jobs


def derive_chromosome_label(event: Dict[str, Any]) -> str:
    """
    Determine a stable chromosome label for partitioning output data.
    """
    chromosome = event.get('chromosome')
    if chromosome:
        return str(chromosome)

    output_prefix = event.get('output_prefix', '')
    match = re.search(r'(?:^|/)chr([^/]+)$', output_prefix, re.IGNORECASE)
    if match:
        return match.group(1)

    accession_id = event.get('accession_id')
    if accession_id:
        return accession_id.split('.')[0]

    return 'unknown'


def build_output_key(event: Dict[str, Any], source: str, dataset_key: str = 'sequences') -> str:
    """
    Build the Hive-style S3 output key for a single chromosome job.
    """
    now = datetime.utcnow()
    species = event.get('species', 'homo_sapiens')
    chromosome = derive_chromosome_label(event)
    output_prefix = event.get('output_prefix', '').strip().strip('/')
    file_stem = output_prefix.split('/')[-1] if output_prefix else chromosome
    file_stem = re.sub(r'[^A-Za-z0-9._-]+', '_', file_stem).strip('._') or chromosome
    dataset_prefix = DATASET_PREFIXES.get(dataset_key, dataset_key)

    return (
        f"{dataset_prefix}/"
        f"source={source}/"
        f"species={species}/"
        f"chr={chromosome}/"
        f"year={now.year}/month={now.month:02d}/"
        f"{file_stem}_{dataset_key}_{now.strftime('%Y%m%d_%H%M%S')}.parquet"
    )


def build_raw_output_key(event: Dict[str, Any], source: str, extension: str = 'fasta') -> str:
    """
    Build the S3 key for the raw downloaded chromosome file.
    """
    now = datetime.utcnow()
    species = event.get('species', 'homo_sapiens')
    chromosome = derive_chromosome_label(event)
    output_prefix = event.get('output_prefix', '').strip().strip('/')
    file_stem = output_prefix.split('/')[-1] if output_prefix else chromosome
    file_stem = re.sub(r'[^A-Za-z0-9._-]+', '_', file_stem).strip('._') or chromosome

    return (
        f"{DATASET_PREFIXES['raw']}/"
        f"source={source}/"
        f"species={species}/"
        f"chr={chromosome}/"
        f"year={now.year}/month={now.month:02d}/"
        f"{file_stem}_raw_{now.strftime('%Y%m%d_%H%M%S')}.{extension}"
    )


def normalize_analysis_mode(value: Any) -> str:
    """
    Normalize requested analysis mode.
    """
    mode = str(value or 'full').strip().lower()
    if mode not in ALLOWED_ANALYSIS_MODES:
        raise ValueError(f"Unsupported analysis_mode: {mode}")
    return mode


def normalize_job_type(value: Any) -> str:
    """
    Normalize requested pipeline job type.
    """
    job_type = str(value or 'sequence_analysis').strip().lower()
    if job_type not in ALLOWED_JOB_TYPES:
        raise ValueError(f"Unsupported job_type: {job_type}")
    return job_type


def chromosome_length(chromosome: str) -> Optional[int]:
    normalized = str(chromosome).upper() if str(chromosome).lower() in {'x', 'y'} else str(chromosome)
    return HUMAN_CHROMOSOME_LENGTHS.get(normalized)


def partition_values_from_key(key: str) -> Optional[Dict[str, str]]:
    match = re.search(
        r"source=([^/]+)/species=([^/]+)/chr=([^/]+)/year=([^/]+)/month=([^/]+)/",
        key,
    )
    if not match:
        return None
    return {
        'source': match.group(1),
        'species': match.group(2),
        'chr': match.group(3),
        'year': match.group(4),
        'month': match.group(5),
    }


def glue_table_descriptor(table_name: str) -> Dict[str, Any]:
    cached = _GLUE_TABLE_CACHE.get(table_name)
    if cached:
        return cached

    response = glue_client.get_table(DatabaseName=GLUE_DATABASE, Name=table_name)
    descriptor = response['Table']['StorageDescriptor']
    _GLUE_TABLE_CACHE[table_name] = descriptor
    return descriptor


def register_glue_partition(dataset_key: str, bucket: str, key: str) -> None:
    table_name = GLUE_TABLES.get(dataset_key)
    partition_values = partition_values_from_key(key)
    if not table_name or not partition_values:
        logger.info("Skipping Glue registration for dataset=%s key=%s", dataset_key, key)
        return

    base_descriptor = glue_table_descriptor(table_name)
    storage_descriptor = {
        'Columns': base_descriptor.get('Columns', []),
        'Location': f"s3://{bucket}/{key.rsplit('/', 1)[0]}/",
        'InputFormat': base_descriptor.get('InputFormat'),
        'OutputFormat': base_descriptor.get('OutputFormat'),
        'Compressed': base_descriptor.get('Compressed', False),
        'SerdeInfo': base_descriptor.get('SerdeInfo', {}),
        'StoredAsSubDirectories': base_descriptor.get('StoredAsSubDirectories', False),
    }
    if 'Parameters' in base_descriptor:
        storage_descriptor['Parameters'] = base_descriptor['Parameters']

    try:
        glue_client.create_partition(
            DatabaseName=GLUE_DATABASE,
            TableName=table_name,
            PartitionInput={
                'Values': [
                    partition_values['source'],
                    partition_values['species'],
                    partition_values['chr'],
                    partition_values['year'],
                    partition_values['month'],
                ],
                'StorageDescriptor': storage_descriptor,
            },
        )
        logger.info(
            "Registered Glue partition for %s: chr=%s year=%s month=%s",
            table_name,
            partition_values['chr'],
            partition_values['year'],
            partition_values['month'],
        )
    except Exception as exc:
        if 'AlreadyExistsException' in str(exc):
            logger.info("Glue partition already exists for %s key=%s", table_name, key)
        else:
            raise

    register_athena_partition(dataset_key, bucket, key)


def run_athena_ddl(query: str, timeout_seconds: int = 30) -> None:
    if not ATHENA_WORKGROUP or not ATHENA_RESULTS_BUCKET:
        logger.info("Skipping Athena DDL because ATHENA_WORKGROUP or ATHENA_RESULTS_BUCKET is not configured")
        return

    response = athena_client.start_query_execution(
        QueryString=query,
        QueryExecutionContext={"Database": ATHENA_DATABASE},
        WorkGroup=ATHENA_WORKGROUP,
        ResultConfiguration={
            "OutputLocation": f"s3://{ATHENA_RESULTS_BUCKET}/query-results/"
        },
    )
    execution_id = response["QueryExecutionId"]
    deadline = datetime.utcnow().timestamp() + timeout_seconds

    while datetime.utcnow().timestamp() < deadline:
        execution = athena_client.get_query_execution(QueryExecutionId=execution_id)
        state = execution["QueryExecution"]["Status"]["State"]
        if state == "SUCCEEDED":
            logger.info("Athena DDL succeeded for QueryExecutionId=%s", execution_id)
            return
        if state in {"FAILED", "CANCELLED"}:
            reason = execution["QueryExecution"]["Status"].get("StateChangeReason", state)
            raise RuntimeError(f"Athena DDL failed: {reason}")

    raise TimeoutError("Athena DDL timed out")


def register_athena_partition(dataset_key: str, bucket: str, key: str) -> None:
    table_name = GLUE_TABLES.get(dataset_key)
    partition_values = partition_values_from_key(key)
    if not table_name or not partition_values:
        logger.info("Skipping Athena registration for dataset=%s key=%s", dataset_key, key)
        return

    location = f"s3://{bucket}/{key.rsplit('/', 1)[0]}/"
    query = f"""
    ALTER TABLE {table_name}
    ADD IF NOT EXISTS PARTITION (
      source='{partition_values['source']}',
      species='{partition_values['species']}',
      chr='{partition_values['chr']}',
      year='{partition_values['year']}',
      month='{partition_values['month']}'
    )
    LOCATION '{location}'
    """
    try:
        run_athena_ddl(query)
        logger.info(
            "Registered Athena partition for %s: chr=%s year=%s month=%s",
            table_name,
            partition_values['chr'],
            partition_values['year'],
            partition_values['month'],
        )
    except Exception as exc:
        logger.warning("Athena partition registration failed for %s key=%s: %s", table_name, key, exc)


def clean_annotation_description(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return str(value).split('[Source:', 1)[0].strip() or None


def normalize_gene_annotation_record(
    item: Dict[str, Any],
    species: str,
    chromosome: str,
    source: str = 'ensembl',
) -> Dict[str, Any]:
    start = int(item.get('start') or 0)
    end = int(item.get('end') or 0)
    length = max(0, end - start + 1) if start and end else 0
    strand_value = int(item.get('strand') or 0)

    return {
        'gene_id': item.get('id'),
        'gene_symbol': item.get('external_name') or item.get('gene_name') or item.get('id'),
        'gene_name': clean_annotation_description(item.get('description')),
        'feature_type': item.get('feature_type') or item.get('feature') or 'gene',
        'biotype': item.get('biotype'),
        'start': start,
        'end': end,
        'length': length,
        'strand': '+' if strand_value >= 0 else '-',
        'assembly_name': item.get('assembly_name'),
        'source_name': source,
        'species': species,
        'chromosome': str(chromosome),
        'version': item.get('version'),
    }


def fetch_ensembl_gene_annotations(species: str, chromosome: str) -> List[Dict[str, Any]]:
    """
    Download Ensembl gene annotations for a chromosome in manageable chunks.
    """
    import requests

    length = chromosome_length(chromosome)
    if not length:
        raise ValueError(f"Unsupported chromosome for annotation fetch: {chromosome}")

    server = "https://rest.ensembl.org"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    records: List[Dict[str, Any]] = []
    seen_ids = set()

    for start in range(1, length + 1, ENSEMBL_GENE_CHUNK_SIZE):
        end = min(length, start + ENSEMBL_GENE_CHUNK_SIZE - 1)
        region = f"{chromosome}:{start}-{end}"
        url = f"{server}/overlap/region/{species}/{region}"
        params = {"feature": "gene"}

        logger.info("Fetching Ensembl gene annotations for %s %s", species, region)
        response = requests.get(url, headers=headers, params=params, timeout=120)
        response.raise_for_status()

        for item in response.json():
            gene_id = item.get('id')
            if gene_id in seen_ids:
                continue
            seen_ids.add(gene_id)
            records.append(normalize_gene_annotation_record(item, species, chromosome))

    logger.info("Fetched %s unique gene annotations for chromosome %s", len(records), chromosome)
    return records


def download_genome_file(url: str, output_path: str) -> bool:
    """
    Download genome sequence file from API/URL
    
    Args:
        url: Source URL for genome data
        output_path: Local path to save file
    
    Returns:
        bool: Success status
    """
    import urllib.request
    
    try:
        logger.info(f"Downloading genome file from {url}")
        urllib.request.urlretrieve(url, output_path)
        logger.info(f"Downloaded to {output_path}")
        return True
    except Exception as e:
        logger.error(f"Error downloading file: {str(e)}")
        return False


def download_from_ncbi(accession_id: str, output_path: str) -> bool:
    """
    Download genome sequences from NCBI using Entrez API
    
    Args:
        accession_id: NCBI accession ID (e.g., 'NC_000001.11' for human chromosome 1)
        output_path: Local path to save FASTA file
    
    Returns:
        bool: Success status
    """
    from Bio import Entrez
    
    try:
        # Set your email for NCBI
        Entrez.email = os.environ.get('NCBI_EMAIL', 'your_email@example.com')
        
        logger.info(f"Fetching sequence {accession_id} from NCBI")
        
        # Fetch sequence
        handle = Entrez.efetch(
            db="nucleotide",
            id=accession_id,
            rettype="fasta",
            retmode="text"
        )
        
        # Write to file
        with open(output_path, 'w') as f:
            f.write(handle.read())
        
        handle.close()
        logger.info(f"NCBI sequence saved to {output_path}")
        return True
        
    except Exception as e:
        logger.error(f"Error fetching from NCBI: {str(e)}")
        return False


def download_from_ensembl(species: str, chromosome: str, output_path: str) -> bool:
    """
    Download genome sequences from Ensembl REST API
    
    Args:
        species: Species name (e.g., 'homo_sapiens')
        chromosome: Chromosome number or name
        output_path: Local path to save FASTA file
    
    Returns:
        bool: Success status
    """
    import requests
    
    try:
        server = "https://rest.ensembl.org"
        ext = f"/sequence/region/{species}/{chromosome}?coord_system_version=GRCh38"
        
        headers = {"Content-Type": "text/x-fasta"}
        
        logger.info(f"Fetching {species} chromosome {chromosome} from Ensembl")
        
        response = requests.get(server + ext, headers=headers)
        response.raise_for_status()
        
        with open(output_path, 'w') as f:
            f.write(response.text)
        
        logger.info(f"Ensembl sequence saved to {output_path}")
        return True
        
    except Exception as e:
        logger.error(f"Error fetching from Ensembl: {str(e)}")
        return False


def parse_with_cpp(input_path: str, output_json_path: str, analysis_mode: str = 'full') -> bool:
    """
    Parse FASTA/FASTQ file using C++ parser
    
    Args:
        input_path: Path to input FASTA/FASTQ file
        output_json_path: Path where C++ parser will write JSON output
    
    Returns:
        bool: Success status
    """
    try:
        logger.info(f"Parsing {input_path} with C++ parser")
        
        # Execute C++ parser
        # Expected command: ./fasta_parser <input_file> <output_json>
        result = subprocess.run(
            [CPP_PARSER_PATH, input_path, output_json_path, analysis_mode],
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )
        
        if result.returncode != 0:
            logger.error(f"C++ parser error: {result.stderr}")
            return False
        
        logger.info(f"C++ parsing completed. Output: {output_json_path}")
        return True
        
    except subprocess.TimeoutExpired:
        logger.error("C++ parser timed out")
        return False
    except Exception as e:
        logger.error(f"Error running C++ parser: {str(e)}")
        return False


def _empty_base_composition() -> Dict[str, int]:
    return {'A': 0, 'T': 0, 'G': 0, 'C': 0, 'N': 0}


def _update_base_composition(composition: Dict[str, int], sequence: str) -> None:
    for base in sequence:
        upper_base = base.upper()
        if upper_base in composition:
            composition[upper_base] += 1
        else:
            composition['N'] += 1


def build_sequence_metadata_json(input_path: str, output_json_path: str) -> bool:
    """
    Build lightweight sequence metadata without invoking the C++ analysis pipeline.
    """
    try:
        with open(input_path, 'r') as handle:
            first_char = handle.read(1)

        if first_char == '>':
            sequences = parse_fasta_metadata(input_path)
        elif first_char == '@':
            sequences = parse_fastq_metadata(input_path)
        else:
            raise ValueError("Unsupported sequence file format for metadata-only mode")

        payload = {
            'format': 'genome_sequences',
            'record_count': len(sequences),
            'sequences': sequences,
            'patterns': [],
            'regions': [],
        }

        with open(output_json_path, 'w') as handle:
            json.dump(payload, handle)

        return True
    except Exception as exc:
        logger.error(f"Error building sequence metadata: {exc}")
        return False


def parse_fasta_metadata(input_path: str) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    current_id = None
    current_description = ''
    current_length = 0
    current_gc_count = 0
    current_composition = _empty_base_composition()

    def flush_record() -> None:
        nonlocal current_id, current_description, current_length, current_gc_count, current_composition
        if current_id is None:
            return
        gc_content = (current_gc_count / current_length * 100.0) if current_length else 0.0
        records.append({
            'id': current_id,
            'description': current_description,
            'sequence': None,
            'length': current_length,
            'gc_content': gc_content,
            'base_composition': dict(current_composition),
        })

    with open(input_path, 'r') as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith('>'):
                flush_record()
                header = line[1:]
                parts = header.split(' ', 1)
                current_id = parts[0]
                current_description = parts[1] if len(parts) > 1 else ''
                current_length = 0
                current_gc_count = 0
                current_composition = _empty_base_composition()
                continue

            current_length += len(line)
            uppercase_line = line.upper()
            current_gc_count += sum(1 for base in uppercase_line if base in {'G', 'C'})
            _update_base_composition(current_composition, uppercase_line)

    flush_record()
    return records


def parse_fastq_metadata(input_path: str) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with open(input_path, 'r') as handle:
        while True:
            header = handle.readline()
            if not header:
                break
            sequence = handle.readline()
            separator = handle.readline()
            quality = handle.readline()
            if not sequence or not separator or not quality:
                break

            header = header.strip()
            sequence = sequence.strip()
            quality = quality.strip()
            if not header.startswith('@'):
                continue

            sequence_id_parts = header[1:].split(' ', 1)
            sequence_id = sequence_id_parts[0]
            description = sequence_id_parts[1] if len(sequence_id_parts) > 1 else ''
            uppercase_sequence = sequence.upper()
            composition = _empty_base_composition()
            _update_base_composition(composition, uppercase_sequence)
            gc_count = sum(1 for base in uppercase_sequence if base in {'G', 'C'})
            length = len(uppercase_sequence)

            records.append({
                'id': sequence_id,
                'description': description,
                'sequence': None,
                'length': length,
                'gc_content': (gc_count / length * 100.0) if length else 0.0,
                'base_composition': composition,
                'quality': None,
            })

    return records


def extract_dataset_records(data: Dict[str, Any], dataset_key: str) -> List[Dict[str, Any]]:
    """
    Extract a dataset payload from the parser JSON output.
    """
    if isinstance(data, list):
        return data
    return data.get(dataset_key, [])


def write_records_to_parquet(records: List[Dict[str, Any]], parquet_path: str) -> bool:
    """
    Write arbitrary record dictionaries to Parquet.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    try:
        if not records:
            logger.info("No records provided for Parquet write to %s", parquet_path)
            return False

        table = pa.Table.from_pylist(records)
        pq.write_table(table, parquet_path, compression='snappy')
        logger.info("Parquet file created: %s", parquet_path)
        return True
    except Exception as exc:
        logger.error("Error writing records to Parquet: %s", exc)
        return False


def convert_to_parquet(json_path: str, parquet_path: str, dataset_key: str = 'sequences') -> bool:
    """
    Convert parsed JSON data to Parquet format
    
    Args:
        json_path: Path to JSON file from C++ parser
        parquet_path: Output path for Parquet file
        dataset_key: Top-level parser dataset to convert
    
    Returns:
        bool: Success status
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    try:
        logger.info(f"Converting {json_path} to Parquet")

        # Read JSON data
        with open(json_path, 'r') as f:
            data = json.load(f)

        records = extract_dataset_records(data, dataset_key)
        if not records:
            logger.info(f"No records found for dataset '{dataset_key}', skipping Parquet write")
            return False

        # Write directly to Parquet via pyarrow (no pandas needed)
        table = pa.Table.from_pylist(records)
        pq.write_table(table, parquet_path, compression='snappy')

        logger.info(f"Parquet file created: {parquet_path}")
        return True
        
    except Exception as e:
        logger.error(f"Error converting to Parquet: {str(e)}")
        return False


def process_gene_annotation_job(job_event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fetch gene annotations for a chromosome and store them as Parquet.
    """
    source = job_event.get('source', 'ensembl')
    if source != 'ensembl':
        raise ValueError("gene_annotations jobs currently support source='ensembl' only")

    species = job_event.get('species', 'homo_sapiens')
    chromosome = str(job_event['chromosome'])
    records = fetch_ensembl_gene_annotations(species, chromosome)

    with tempfile.TemporaryDirectory() as temp_dir:
        parquet_path = os.path.join(temp_dir, "gene_annotations.parquet")
        if not write_records_to_parquet(records, parquet_path):
            raise Exception("Failed to build gene annotation Parquet")

        output_key = build_output_key(job_event, source, dataset_key='annotations')
        if not upload_to_s3(parquet_path, OUTPUT_BUCKET, output_key):
            raise Exception("Failed to upload gene annotations to S3")
        register_glue_partition('annotations', OUTPUT_BUCKET, output_key)

    return {
        'job_type': 'gene_annotations',
        'chromosome': chromosome,
        'species': species,
        'record_count': len(records),
        'annotation_key': output_key,
    }


def upload_to_s3(file_path: str, bucket: str, key: str) -> bool:
    """
    Upload file to S3
    
    Args:
        file_path: Local file path
        bucket: S3 bucket name
        key: S3 object key
    
    Returns:
        bool: Success status
    """
    try:
        logger.info(f"Uploading {file_path} to s3://{bucket}/{key}")
        
        s3_client.upload_file(file_path, bucket, key)
        
        logger.info(f"Upload complete")
        return True
        
    except Exception as e:
        logger.error(f"Error uploading to S3: {str(e)}")
        return False


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda handler for genome data pipeline
    
    Expected event structure:
    {
        "source": "ncbi" | "ensembl" | "url",
        "accession_id": "NC_000001.11",  # for NCBI
        "species": "homo_sapiens",        # for Ensembl
        "chromosome": "1",                # for Ensembl
        "url": "https://...",             # for direct URL
        "output_prefix": "human_genome/chr1"
    }
    """
    try:
        logger.info(f"Received event: {json.dumps(event)}")
        job_events = extract_job_events(event)
        results = []

        for job_event in job_events:
            backend = 'batch' if os.environ.get('AWS_BATCH_JOB_ID') else 'lambda'
            record_start(
                job_event,
                backend,
                message_id=job_event.get('message_id'),
                batch_job_id=os.environ.get('AWS_BATCH_JOB_ID'),
                batch_job_name=os.environ.get('AWS_BATCH_JOB_NAME'),
            )

            try:
                job_type = normalize_job_type(job_event.get('job_type', 'sequence_analysis'))

                if job_type == 'gene_annotations':
                    annotation_result = process_gene_annotation_job(job_event)
                    annotation_outputs = {
                        'annotations': f"s3://{OUTPUT_BUCKET}/{annotation_result['annotation_key']}",
                    }
                    record_success(
                        job_event,
                        backend,
                        outputs=annotation_outputs,
                        extra_fields={
                            'record_count': annotation_result.get('record_count'),
                        },
                    )
                    results.append(annotation_result)
                    continue

                with tempfile.TemporaryDirectory() as temp_dir:
                    input_file = os.path.join(temp_dir, "input.fasta")
                    json_file = os.path.join(temp_dir, "parsed.json")
                    parquet_files = {
                        'sequences': os.path.join(temp_dir, "sequences.parquet"),
                        'patterns': os.path.join(temp_dir, "patterns.parquet"),
                        'regions': os.path.join(temp_dir, "regions.parquet"),
                    }

                    source = job_event.get('source', 'ncbi')
                    analysis_mode = normalize_analysis_mode(job_event.get('analysis_mode', 'full'))

                    if source == 'ncbi':
                        accession_id = job_event['accession_id']
                        success = download_from_ncbi(accession_id, input_file)
                    elif source == 'ensembl':
                        species = job_event.get('species', 'homo_sapiens')
                        chromosome = job_event['chromosome']
                        success = download_from_ensembl(species, chromosome, input_file)
                    elif source == 'url':
                        url = job_event['url']
                        success = download_genome_file(url, input_file)
                    else:
                        raise ValueError(f"Unknown source: {source}")

                    if not success:
                        raise Exception("Failed to download genome data")

                    raw_key = build_raw_output_key(job_event, source)
                    if not upload_to_s3(input_file, OUTPUT_BUCKET, raw_key):
                        raise Exception("Failed to upload raw genome data to S3")

                    if analysis_mode == 'sequence_only':
                        if not build_sequence_metadata_json(input_file, json_file):
                            raise Exception("Failed to build sequence metadata")
                    elif not parse_with_cpp(input_file, json_file, analysis_mode=analysis_mode):
                        raise Exception("Failed to parse genome data")

                    uploaded_outputs = {}
                    uploaded_outputs['raw'] = f"s3://{OUTPUT_BUCKET}/{raw_key}"
                    dataset_keys = ['sequences']
                    if analysis_mode == 'full':
                        dataset_keys.extend(['patterns', 'regions'])

                    for dataset_key in dataset_keys:
                        parquet_file = parquet_files[dataset_key]
                        if not convert_to_parquet(json_file, parquet_file, dataset_key=dataset_key):
                            continue

                        s3_key = build_output_key(job_event, source, dataset_key=dataset_key)
                        if not upload_to_s3(parquet_file, OUTPUT_BUCKET, s3_key):
                            raise Exception(f"Failed to upload {dataset_key} output to S3")
                        register_glue_partition(dataset_key, OUTPUT_BUCKET, s3_key)
                        uploaded_outputs[dataset_key] = f"s3://{OUTPUT_BUCKET}/{s3_key}"

                    if 'sequences' not in uploaded_outputs:
                        raise Exception("Failed to produce sequence Parquet output")

                    record_success(job_event, backend, outputs=uploaded_outputs)
                    results.append({
                        'output_location': uploaded_outputs['sequences'],
                        'analysis_outputs': uploaded_outputs,
                        'source': source,
                        'chromosome': derive_chromosome_label(job_event),
                        'analysis_mode': analysis_mode,
                    })
            except Exception as exc:
                record_failure(
                    job_event,
                    backend,
                    failure_reason=str(exc),
                    error_type=exc.__class__.__name__,
                )
                raise

        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Pipeline completed successfully',
                'results': results
            })
        }
            
    except Exception as e:
        logger.error(f"Pipeline failed: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'message': 'Pipeline failed',
                'error': str(e)
            })
        }
