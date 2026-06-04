"""
API Client for Genome Data Pipeline
Provides convenient methods to trigger pipeline processing
"""
import boto3
import json
from typing import Dict, List, Optional, Any
from datetime import datetime


class GenomePipelineClient:
    """Client for interacting with the Genome Data Pipeline"""
    
    def __init__(self, region: str = 'us-east-1'):
        """
        Initialize the pipeline client
        
        Args:
            region: AWS region where pipeline is deployed
        """
        self.region = region
        self.sqs = boto3.client('sqs', region_name=region)
        self.sfn = boto3.client('stepfunctions', region_name=region)
        self.lambda_client = boto3.client('lambda', region_name=region)
        self.s3 = boto3.client('s3', region_name=region)
        
    def submit_ncbi_job(
        self,
        accession_id: str,
        output_prefix: Optional[str] = None,
        queue_url: Optional[str] = None,
        analysis_mode: str = 'full'
    ) -> Dict[str, Any]:
        """
        Submit a job to process NCBI genome sequence
        
        Args:
            accession_id: NCBI accession ID (e.g., 'NC_000001.11')
            output_prefix: Optional job label used in the output filename
            queue_url: SQS queue URL (if not provided, uses Lambda invoke)
        
        Returns:
            Response from submission
        """
        if output_prefix is None:
            output_prefix = f"ncbi/{accession_id}"
        
        message = {
            'source': 'ncbi',
            'accession_id': accession_id,
            'output_prefix': output_prefix,
            'analysis_mode': analysis_mode,
            'submitted_at': datetime.utcnow().isoformat()
        }
        
        if queue_url:
            return self._submit_to_sqs(message, queue_url)
        else:
            return self._invoke_lambda(message)
    
    def submit_ensembl_job(
        self,
        chromosome: str,
        species: str = 'homo_sapiens',
        output_prefix: Optional[str] = None,
        queue_url: Optional[str] = None,
        analysis_mode: str = 'full'
    ) -> Dict[str, Any]:
        """
        Submit a job to process Ensembl genome sequence
        
        Args:
            chromosome: Chromosome identifier
            species: Species name (default: 'homo_sapiens')
            output_prefix: Optional job label used in the output filename
            queue_url: SQS queue URL (if not provided, uses Lambda invoke)
        
        Returns:
            Response from submission
        """
        if output_prefix is None:
            output_prefix = f"ensembl/{species}/chr{chromosome}"
        
        message = {
            'source': 'ensembl',
            'species': species,
            'chromosome': chromosome,
            'output_prefix': output_prefix,
            'analysis_mode': analysis_mode,
            'submitted_at': datetime.utcnow().isoformat()
        }
        
        if queue_url:
            return self._submit_to_sqs(message, queue_url)
        else:
            return self._invoke_lambda(message)
    
    def submit_url_job(
        self,
        url: str,
        output_prefix: Optional[str] = None,
        queue_url: Optional[str] = None,
        analysis_mode: str = 'full'
    ) -> Dict[str, Any]:
        """
        Submit a job to process genome sequence from URL
        
        Args:
            url: URL to download genome data
            output_prefix: Optional job label used in the output filename
            queue_url: SQS queue URL (if not provided, uses Lambda invoke)
        
        Returns:
            Response from submission
        """
        if output_prefix is None:
            output_prefix = f"custom/{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        
        message = {
            'source': 'url',
            'url': url,
            'output_prefix': output_prefix,
            'analysis_mode': analysis_mode,
            'submitted_at': datetime.utcnow().isoformat()
        }
        
        if queue_url:
            return self._submit_to_sqs(message, queue_url)
        else:
            return self._invoke_lambda(message)

    def submit_gene_annotation_job(
        self,
        chromosome: str,
        species: str = 'homo_sapiens',
        queue_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Submit a job to fetch known gene annotations for a chromosome.

        Args:
            chromosome: Chromosome identifier
            species: Species name (default: homo_sapiens)
            queue_url: SQS queue URL (if not provided, uses Lambda invoke)

        Returns:
            Response from submission
        """
        message = {
            'job_type': 'gene_annotations',
            'source': 'ensembl',
            'species': species,
            'chromosome': chromosome,
            'output_prefix': f'annotations/chr{chromosome}',
            'submitted_at': datetime.utcnow().isoformat(),
        }

        if queue_url:
            return self._submit_to_sqs(message, queue_url)
        else:
            return self._invoke_lambda(message)
    
    def submit_batch_ncbi(
        self,
        accession_ids: List[str],
        queue_url: str
    ) -> List[Dict[str, Any]]:
        """
        Submit multiple NCBI jobs in batch
        
        Args:
            accession_ids: List of NCBI accession IDs
            queue_url: SQS queue URL
        
        Returns:
            List of submission responses
        """
        results = []
        for acc_id in accession_ids:
            result = self.submit_ncbi_job(acc_id, queue_url=queue_url)
            results.append(result)
        return results
    
    def submit_human_chromosomes(
        self,
        chromosomes: Optional[List[str]] = None,
        queue_url: Optional[str] = None,
        analysis_mode: str = 'sequence_only'
    ) -> List[Dict[str, Any]]:
        """
        Submit jobs for human chromosomes from NCBI
        
        Args:
            chromosomes: List of chromosome numbers (default: all 1-22, X, Y)
            queue_url: SQS queue URL
        
        Returns:
            List of submission responses
        """
        # NCBI RefSeq accession IDs for human chromosomes (GRCh38.p14)
        chromosome_accessions = {
            '1': 'NC_000001.11',
            '2': 'NC_000002.12',
            '3': 'NC_000003.12',
            '4': 'NC_000004.12',
            '5': 'NC_000005.10',
            '6': 'NC_000006.12',
            '7': 'NC_000007.14',
            '8': 'NC_000008.11',
            '9': 'NC_000009.12',
            '10': 'NC_000010.11',
            '11': 'NC_000011.10',
            '12': 'NC_000012.12',
            '13': 'NC_000013.11',
            '14': 'NC_000014.9',
            '15': 'NC_000015.10',
            '16': 'NC_000016.10',
            '17': 'NC_000017.11',
            '18': 'NC_000018.10',
            '19': 'NC_000019.10',
            '20': 'NC_000020.11',
            '21': 'NC_000021.9',
            '22': 'NC_000022.11',
            'X': 'NC_000023.11',
            'Y': 'NC_000024.10'
        }
        
        if chromosomes is None:
            chromosomes = [str(i) for i in range(1, 23)] + ['X', 'Y']
        
        results = []
        for chr_num in chromosomes:
            if chr_num in chromosome_accessions:
                acc_id = chromosome_accessions[chr_num]
                result = self.submit_ncbi_job(
                    acc_id,
                    output_prefix=f"human_genome/chr{chr_num}",
                    queue_url=queue_url,
                    analysis_mode=analysis_mode,
                )
                results.append({
                    'chromosome': chr_num,
                    'accession': acc_id,
                    'result': result
                })
        
        return results
    
    def start_state_machine(
        self,
        state_machine_arn: str,
        input_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Start Step Functions state machine execution
        
        Args:
            state_machine_arn: ARN of the state machine
            input_data: Input data for the execution
        
        Returns:
            Execution details
        """
        response = self.sfn.start_execution(
            stateMachineArn=state_machine_arn,
            input=json.dumps(input_data)
        )
        return response
    
    def check_execution_status(self, execution_arn: str) -> Dict[str, Any]:
        """
        Check status of a Step Functions execution
        
        Args:
            execution_arn: ARN of the execution
        
        Returns:
            Execution status details
        """
        response = self.sfn.describe_execution(
            executionArn=execution_arn
        )
        return response
    
    def list_output_files(
        self,
        bucket: str,
        prefix: str = ''
    ) -> List[Dict[str, Any]]:
        """
        List output files in S3 bucket
        
        Args:
            bucket: S3 bucket name
            prefix: Key prefix to filter
        
        Returns:
            List of S3 objects
        """
        response = self.s3.list_objects_v2(
            Bucket=bucket,
            Prefix=prefix
        )
        
        if 'Contents' in response:
            return response['Contents']
        return []
    
    def download_output(
        self,
        bucket: str,
        key: str,
        local_path: str
    ) -> str:
        """
        Download output file from S3
        
        Args:
            bucket: S3 bucket name
            key: S3 object key
            local_path: Local file path to save
        
        Returns:
            Local file path
        """
        self.s3.download_file(bucket, key, local_path)
        return local_path
    
    def _submit_to_sqs(
        self,
        message: Dict[str, Any],
        queue_url: str
    ) -> Dict[str, Any]:
        """Submit message to SQS queue"""
        response = self.sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(message)
        )
        return response
    
    def _invoke_lambda(
        self,
        payload: Dict[str, Any],
        function_name: str = 'genome-pipeline-processor'
    ) -> Dict[str, Any]:
        """Invoke Lambda function directly"""
        response = self.lambda_client.invoke(
            FunctionName=function_name,
            InvocationType='Event',  # Asynchronous
            Payload=json.dumps(payload)
        )
        return {
            'StatusCode': response['StatusCode'],
            'RequestId': response['ResponseMetadata']['RequestId']
        }


# Example usage
if __name__ == '__main__':
    # Initialize client
    client = GenomePipelineClient(region='us-east-1')
    
    # Example 1: Process single chromosome
    result = client.submit_ncbi_job(
        accession_id='NC_000001.11',
        output_prefix='human_genome/chr1'
    )
    print(f"Single job submitted: {result}")
    
    # Example 2: Process all human chromosomes
    # queue_url = 'https://sqs.us-east-1.amazonaws.com/123456789/genome-pipeline-queue'
    # results = client.submit_human_chromosomes(queue_url=queue_url)
    # print(f"Submitted {len(results)} chromosome jobs")
    
    # Example 3: Process from Ensembl
    # result = client.submit_ensembl_job(
    #     chromosome='1',
    #     species='homo_sapiens'
    # )
    
    # Example 4: List outputs
    # outputs = client.list_output_files(
    #     bucket='genome-pipeline-output-123456789',
    #     prefix='human_genome/'
    # )
    # for obj in outputs:
    #     print(f"  {obj['Key']} - {obj['Size']} bytes")
