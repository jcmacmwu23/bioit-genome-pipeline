#!/usr/bin/env python3
"""
Example usage script for Genome Data Pipeline
Demonstrates various ways to use the pipeline
"""
import sys
from pipeline_client import GenomePipelineClient


def example_1_single_chromosome():
    """Example 1: Process a single human chromosome from NCBI"""
    print("\n=== Example 1: Process Single Chromosome ===\n")
    
    client = GenomePipelineClient(region='us-east-1')
    
    # Process human chromosome 21 (smallest autosomal chromosome)
    result = client.submit_ncbi_job(
        accession_id='NC_000021.9',
        output_prefix='human_genome/chr21'
    )
    
    print(f"Job submitted successfully!")
    print(f"Status: {result['StatusCode']}")
    print(f"Request ID: {result.get('RequestId', 'N/A')}")
    print(f"\nOutput will be available at: s3://OUTPUT_BUCKET/human_genome/chr21.parquet")


def example_2_all_chromosomes():
    """Example 2: Process all human chromosomes using SQS queue"""
    print("\n=== Example 2: Process All Human Chromosomes ===\n")
    
    # Replace with your actual queue URL from Terraform outputs
    queue_url = 'https://sqs.us-east-1.amazonaws.com/ACCOUNT_ID/genome-pipeline-queue'
    
    print("NOTE: Update the queue_url variable with your actual SQS queue URL")
    print("You can get this from: terraform output sqs_queue_url")
    print()
    
    # Uncomment to run:
    # client = GenomePipelineClient(region='us-east-1')
    # results = client.submit_human_chromosomes(queue_url=queue_url)
    # 
    # print(f"Submitted {len(results)} chromosome processing jobs")
    # for result in results[:3]:  # Show first 3
    #     print(f"  - Chromosome {result['chromosome']}: {result['accession']}")


def example_3_ensembl_data():
    """Example 3: Process genome data from Ensembl"""
    print("\n=== Example 3: Process from Ensembl ===\n")
    
    client = GenomePipelineClient(region='us-east-1')
    
    # Process human chromosome 1 from Ensembl
    result = client.submit_ensembl_job(
        chromosome='1',
        species='homo_sapiens',
        output_prefix='ensembl/human/chr1'
    )
    
    print(f"Job submitted successfully!")
    print(f"Status: {result['StatusCode']}")
    print(f"\nOutput will be available at: s3://OUTPUT_BUCKET/ensembl/human/chr1.parquet")


def example_4_custom_url():
    """Example 4: Process genome data from custom URL"""
    print("\n=== Example 4: Process from Custom URL ===\n")
    
    client = GenomePipelineClient(region='us-east-1')
    
    # Example: Process a custom FASTA file
    custom_url = 'https://example.com/path/to/genome.fasta'
    
    print(f"NOTE: Replace custom_url with your actual genome data URL")
    print(f"Current URL: {custom_url}")
    print()
    
    # Uncomment to run:
    # result = client.submit_url_job(
    #     url=custom_url,
    #     output_prefix='custom/my_genome'
    # )
    # print(f"Job submitted successfully!")


def example_5_batch_processing():
    """Example 5: Batch process multiple sequences"""
    print("\n=== Example 5: Batch Processing ===\n")
    
    queue_url = 'https://sqs.us-east-1.amazonaws.com/ACCOUNT_ID/genome-pipeline-queue'
    
    print("NOTE: Update the queue_url variable")
    print()
    
    # List of accession IDs to process
    accessions = [
        'NC_000021.9',   # Chromosome 21
        'NC_000022.11',  # Chromosome 22
        'NC_000023.11',  # Chromosome X
        'NC_000024.10',  # Chromosome Y
    ]
    
    # Uncomment to run:
    # client = GenomePipelineClient(region='us-east-1')
    # results = client.submit_batch_ncbi(accessions, queue_url)
    # 
    # print(f"Submitted {len(results)} batch jobs")
    # for i, result in enumerate(results):
    #     print(f"  Job {i+1}: MessageId = {result.get('MessageId', 'N/A')}")


def example_6_step_functions():
    """Example 6: Use Step Functions for orchestration"""
    print("\n=== Example 6: Step Functions Orchestration ===\n")
    
    # Replace with your actual state machine ARN from Terraform outputs
    state_machine_arn = 'arn:aws:states:us-east-1:ACCOUNT_ID:stateMachine:genome-pipeline-state-machine'
    
    print("NOTE: Update state_machine_arn with your actual ARN")
    print("You can get this from: terraform output state_machine_arn")
    print()
    
    # Uncomment to run:
    # client = GenomePipelineClient(region='us-east-1')
    # 
    # input_data = {
    #     'source': 'ncbi',
    #     'accession_id': 'NC_000021.9',
    #     'output_prefix': 'human_genome/chr21'
    # }
    # 
    # result = client.start_state_machine(state_machine_arn, input_data)
    # execution_arn = result['executionArn']
    # 
    # print(f"Execution started!")
    # print(f"Execution ARN: {execution_arn}")
    # 
    # # Check status
    # import time
    # time.sleep(5)
    # status = client.check_execution_status(execution_arn)
    # print(f"Status: {status['status']}")


def example_7_list_outputs():
    """Example 7: List and download output files"""
    print("\n=== Example 7: List and Download Outputs ===\n")
    
    bucket_name = 'genome-pipeline-output-ACCOUNT_ID'
    
    print("NOTE: Update bucket_name with your actual bucket")
    print("You can get this from: terraform output output_bucket_name")
    print()
    
    # Uncomment to run:
    # client = GenomePipelineClient(region='us-east-1')
    # 
    # # List all output files
    # outputs = client.list_output_files(bucket_name, prefix='human_genome/')
    # 
    # print(f"Found {len(outputs)} output files:")
    # for obj in outputs[:5]:  # Show first 5
    #     size_mb = obj['Size'] / (1024 * 1024)
    #     print(f"  {obj['Key']} - {size_mb:.2f} MB")
    # 
    # # Download a specific file
    # if outputs:
    #     first_file = outputs[0]['Key']
    #     local_path = f"./{first_file.split('/')[-1]}"
    #     client.download_output(bucket_name, first_file, local_path)
    #     print(f"\nDownloaded {first_file} to {local_path}")


def example_8_read_parquet():
    """Example 8: Read and analyze Parquet output"""
    print("\n=== Example 8: Read and Analyze Parquet Output ===\n")
    
    print("After downloading a Parquet file, you can read it with pandas:")
    print()
    print("```python")
    print("import pandas as pd")
    print("")
    print("# Read Parquet file")
    print("df = pd.read_parquet('chr21.parquet')")
    print("")
    print("# Show basic info")
    print("print(f'Number of sequences: {len(df)}')")
    print("print(f'Columns: {df.columns.tolist()}')")
    print("")
    print("# Analyze GC content")
    print("print(f'Average GC content: {df[\"gc_content\"].mean():.2f}%')")
    print("")
    print("# Filter sequences by length")
    print("long_sequences = df[df['length'] > 1000]")
    print("print(f'Sequences > 1000bp: {len(long_sequences)}')")
    print("")
    print("# Access sequence data")
    print("first_seq = df.iloc[0]")
    print("print(f'ID: {first_seq[\"id\"]}')")
    print("print(f'Length: {first_seq[\"length\"]}')")
    print("print(f'GC%: {first_seq[\"gc_content\"]}')")
    print("```")


def example_9_monitoring():
    """Example 9: Monitor pipeline execution"""
    print("\n=== Example 9: Monitor Pipeline Execution ===\n")
    
    print("Monitor Lambda logs:")
    print("```bash")
    print("# Tail logs in real-time")
    print("aws logs tail /aws/lambda/genome-pipeline-processor --follow")
    print("")
    print("# Filter for errors")
    print("aws logs tail /aws/lambda/genome-pipeline-processor --filter-pattern ERROR")
    print("")
    print("# Use Makefile")
    print("make logs")
    print("```")
    print()
    print("Check SQS queue depth:")
    print("```bash")
    print("aws sqs get-queue-attributes \\")
    print("  --queue-url YOUR_QUEUE_URL \\")
    print("  --attribute-names ApproximateNumberOfMessages")
    print("```")


def example_10_cost_optimization():
    """Example 10: Cost optimization tips"""
    print("\n=== Example 10: Cost Optimization Tips ===\n")
    
    print("1. Process smaller chromosomes first to test:")
    print("   - Chr 21 (smallest): NC_000021.9")
    print("   - Chr 22: NC_000022.11")
    print()
    print("2. Use SQS queue for batch processing to avoid Lambda throttling")
    print()
    print("3. Set appropriate Lambda memory (more memory = faster CPU):")
    print("   - Small sequences: 1024 MB")
    print("   - Large chromosomes: 3008 MB (maximum)")
    print()
    print("4. Enable S3 lifecycle policies to move old data to cheaper storage")
    print()
    print("5. Monitor costs with AWS Cost Explorer and set budgets")


def main():
    """Main function to run examples"""
    examples = {
        '1': ('Single Chromosome', example_1_single_chromosome),
        '2': ('All Chromosomes', example_2_all_chromosomes),
        '3': ('Ensembl Data', example_3_ensembl_data),
        '4': ('Custom URL', example_4_custom_url),
        '5': ('Batch Processing', example_5_batch_processing),
        '6': ('Step Functions', example_6_step_functions),
        '7': ('List Outputs', example_7_list_outputs),
        '8': ('Read Parquet', example_8_read_parquet),
        '9': ('Monitoring', example_9_monitoring),
        '10': ('Cost Optimization', example_10_cost_optimization),
    }
    
    if len(sys.argv) > 1:
        # Run specific example
        example_num = sys.argv[1]
        if example_num in examples:
            _, func = examples[example_num]
            func()
        else:
            print(f"Unknown example: {example_num}")
            print_menu(examples)
    else:
        # Show menu
        print_menu(examples)


def print_menu(examples):
    """Print example menu"""
    print("\n" + "="*60)
    print("Genome Data Pipeline - Example Usage")
    print("="*60)
    print()
    print("Available examples:")
    for num, (name, _) in examples.items():
        print(f"  {num}. {name}")
    print()
    print("Usage: python examples.py <example_number>")
    print("Example: python examples.py 1")
    print()
    print("Or import and use directly:")
    print("  from examples import example_1_single_chromosome")
    print("  example_1_single_chromosome()")
    print()


if __name__ == '__main__':
    main()
