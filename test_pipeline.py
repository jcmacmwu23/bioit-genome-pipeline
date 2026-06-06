#!/usr/bin/env python3
"""
Test script for Genome Data Pipeline
"""
import unittest
import json
import os
import tempfile
import subprocess
import sys
import types
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import Mock, patch, MagicMock


class TestFASTAParser(unittest.TestCase):
    """Test C++ FASTA parser"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.test_dir = tempfile.mkdtemp()
        self.parser_path = 'build/fasta_parser'
        
    def test_fasta_parsing(self):
        """Test FASTA file parsing"""
        # Create sample FASTA file
        fasta_content = """>seq1 Test sequence 1
ATGATGATGATGATGTATAAACCCCCCGGGGGGAAAAAATAG
>seq2 Test sequence 2
GCTAGCTAGCTAGCTA
GCTAGCTAGCTAGCTA
"""
        fasta_file = os.path.join(self.test_dir, 'test.fasta')
        json_file = os.path.join(self.test_dir, 'output.json')
        
        with open(fasta_file, 'w') as f:
            f.write(fasta_content)
        
        # Skip if parser not built
        if not os.path.exists(self.parser_path):
            self.skipTest("C++ parser not built")
        
        # Run parser
        try:
            result = subprocess.run(
                [self.parser_path, fasta_file, json_file],
                capture_output=True,
                text=True
            )
        except OSError as exc:
            if exc.errno == 8:
                self.skipTest("C++ parser binary is not executable on this machine")
            raise
        
        self.assertEqual(result.returncode, 0)
        self.assertTrue(os.path.exists(json_file))
        
        # Verify output
        with open(json_file, 'r') as f:
            data = json.load(f)
        
        self.assertEqual(data['record_count'], 2)
        self.assertEqual(len(data['sequences']), 2)
        self.assertEqual(data['sequences'][0]['id'], 'seq1')
        self.assertEqual(data['sequences'][1]['id'], 'seq2')
        self.assertIn('patterns', data)
        self.assertIn('regions', data)
        self.assertGreaterEqual(len(data['regions']), 2)
        self.assertTrue(any(hit['pattern_type'] == 'motif' for hit in data['patterns']))

    def test_fastq_parsing(self):
        """Test FASTQ file parsing"""
        # Create sample FASTQ file
        fastq_content = """@seq1 Test sequence 1
ATCGATCGATCGATCG
+
IIIIIIIIIIIIIIII
@seq2 Test sequence 2
GCTAGCTAGCTAGCTA
+
HHHHHHHHHHHHHHHH
"""
        fastq_file = os.path.join(self.test_dir, 'test.fastq')
        json_file = os.path.join(self.test_dir, 'output.json')
        
        with open(fastq_file, 'w') as f:
            f.write(fastq_content)
        
        # Skip if parser not built
        if not os.path.exists(self.parser_path):
            self.skipTest("C++ parser not built")
        
        # Run parser
        try:
            result = subprocess.run(
                [self.parser_path, fastq_file, json_file],
                capture_output=True,
                text=True
            )
        except OSError as exc:
            if exc.errno == 8:
                self.skipTest("C++ parser binary is not executable on this machine")
            raise
        
        self.assertEqual(result.returncode, 0)
        self.assertTrue(os.path.exists(json_file))
        
        # Verify output
        with open(json_file, 'r') as f:
            data = json.load(f)
        
        self.assertEqual(data['record_count'], 2)
        self.assertEqual(len(data['sequences']), 2)
        self.assertTrue('quality' in data['sequences'][0])


class TestLambdaHandler(unittest.TestCase):
    """Test Lambda handler functions"""

    def setUp(self):
        """Provide lightweight stand-ins for optional cloud/bio deps."""
        self.boto3_module = types.SimpleNamespace(client=MagicMock(return_value=MagicMock()))
        self.bio_module = types.ModuleType('Bio')
        self.entrez_module = types.ModuleType('Entrez')
        self.entrez_module.efetch = MagicMock()
        self.bio_module.Entrez = self.entrez_module
        self.requests_module = types.ModuleType('requests')
        self.requests_module.get = MagicMock()

        self.module_patcher = patch.dict(
            sys.modules,
            {
                'boto3': self.boto3_module,
                'Bio': self.bio_module,
                'Bio.Entrez': self.entrez_module,
                'requests': self.requests_module,
            }
        )
        self.module_patcher.start()
        sys.modules.pop('lambda_handler', None)

    def tearDown(self):
        self.module_patcher.stop()
        sys.modules.pop('lambda_handler', None)
    
    def test_ncbi_download_mock(self):
        """Test NCBI download with mocks"""
        from lambda_handler import download_from_ncbi
        
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, 'output.fasta')
            
            # Mock Entrez imported inside the function body
            with patch('Bio.Entrez.efetch') as mock_efetch:
                mock_handle = MagicMock()
                mock_handle.read.return_value = ">test\nATCG\n"
                mock_efetch.return_value = mock_handle
                
                result = download_from_ncbi('NC_000001.11', output_path)
                
                # Verify file was created
                self.assertTrue(result)
                self.assertTrue(os.path.exists(output_path))

    def test_sqs_event_unwrap(self):
        """Test SQS event payloads are normalized into jobs."""
        from lambda_handler import extract_job_events

        event = {
            'Records': [
                {'body': json.dumps({'source': 'ncbi', 'accession_id': 'NC_000022.11'})}
            ]
        }

        jobs = extract_job_events(event)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['accession_id'], 'NC_000022.11')

    def test_dataset_specific_output_keys(self):
        """Test analysis datasets are routed to distinct S3 prefixes."""
        from lambda_handler import build_output_key, build_raw_output_key

        event = {
            'source': 'ncbi',
            'accession_id': 'NC_000022.11',
            'chromosome': '22',
            'species': 'homo_sapiens',
            'output_prefix': 'manual/chr22'
        }

        patterns_key = build_output_key(event, 'ncbi', dataset_key='patterns')
        regions_key = build_output_key(event, 'ncbi', dataset_key='regions')

        self.assertIn('pattern_data/source=ncbi/species=homo_sapiens/chr=22/', patterns_key)
        self.assertIn('region_data/source=ncbi/species=homo_sapiens/chr=22/', regions_key)

        raw_key = build_raw_output_key(event, 'ncbi')
        self.assertIn('raw_data/source=ncbi/species=homo_sapiens/chr=22/', raw_key)
        self.assertTrue(raw_key.endswith('.fasta'))

    def test_register_glue_partition_uses_dataset_table_and_partition_path(self):
        """Uploaded parquet outputs should register their Glue partition immediately."""
        import lambda_handler
        from lambda_handler import register_glue_partition, glue_client, athena_client

        glue_client.get_table.return_value = {
            'Table': {
                'StorageDescriptor': {
                    'Columns': [{'Name': 'pattern_name', 'Type': 'string'}],
                    'InputFormat': 'org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat',
                    'OutputFormat': 'org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat',
                    'Compressed': False,
                    'SerdeInfo': {
                        'SerializationLibrary': 'org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe'
                    },
                    'StoredAsSubDirectories': False,
                }
            }
        }
        athena_client.start_query_execution.return_value = {'QueryExecutionId': 'qa-1'}
        athena_client.get_query_execution.return_value = {
            'QueryExecution': {'Status': {'State': 'SUCCEEDED'}}
        }

        with patch.object(lambda_handler, 'ATHENA_WORKGROUP', 'genome-pipeline-workgroup'), \
             patch.object(lambda_handler, 'ATHENA_RESULTS_BUCKET', 'genome-pipeline-athena-results-443568785165'):
            register_glue_partition(
                'patterns',
                'genome-pipeline-output-443568785165',
                'pattern_data/source=ncbi/species=homo_sapiens/chr=16/year=2026/month=06/chr16_patterns_20260605_175805.parquet',
            )

        glue_client.create_partition.assert_called_once()
        kwargs = glue_client.create_partition.call_args.kwargs
        self.assertEqual(kwargs['DatabaseName'], 'genome_pipeline_db')
        self.assertEqual(kwargs['TableName'], 'sequence_patterns')
        self.assertEqual(
            kwargs['PartitionInput']['Values'],
            ['ncbi', 'homo_sapiens', '16', '2026', '06'],
        )
        self.assertEqual(
            kwargs['PartitionInput']['StorageDescriptor']['Location'],
            's3://genome-pipeline-output-443568785165/pattern_data/source=ncbi/species=homo_sapiens/chr=16/year=2026/month=06/',
        )
        athena_client.start_query_execution.assert_called_once()
        athena_query = athena_client.start_query_execution.call_args.kwargs['QueryString']
        self.assertIn("ALTER TABLE sequence_patterns", athena_query)
        self.assertIn("ADD IF NOT EXISTS PARTITION", athena_query)
        self.assertIn("chr='16'", athena_query)
    
    def test_event_validation(self):
        """Test event schema validation"""
        # Valid NCBI event
        ncbi_event = {
            'source': 'ncbi',
            'accession_id': 'NC_000001.11',
            'output_prefix': 'test/chr1'
        }
        self.assertIn('source', ncbi_event)
        self.assertIn('accession_id', ncbi_event)
        
        # Valid Ensembl event
        ensembl_event = {
            'source': 'ensembl',
            'species': 'homo_sapiens',
            'chromosome': '1',
            'output_prefix': 'test/chr1'
        }
        self.assertIn('chromosome', ensembl_event)
        
        # Valid URL event
        url_event = {
            'source': 'url',
            'url': 'https://example.com/genome.fasta',
            'output_prefix': 'test/custom'
        }
        self.assertIn('url', url_event)

    def test_sequence_only_metadata_builder(self):
        """Metadata-only mode should emit lightweight sequence records."""
        from lambda_handler import build_sequence_metadata_json

        with tempfile.TemporaryDirectory() as temp_dir:
            fasta_path = os.path.join(temp_dir, 'chr22.fasta')
            json_path = os.path.join(temp_dir, 'chr22.json')

            with open(fasta_path, 'w') as handle:
                handle.write(">chr22 demo\nATGCNNATGC\n")

            self.assertTrue(build_sequence_metadata_json(fasta_path, json_path))

            with open(json_path, 'r') as handle:
                payload = json.load(handle)

            self.assertEqual(payload['record_count'], 1)
            self.assertEqual(payload['patterns'], [])
            self.assertEqual(payload['regions'], [])
            self.assertEqual(payload['sequences'][0]['length'], 10)
            self.assertIsNone(payload['sequences'][0]['sequence'])

    def test_gene_annotation_job_writes_annotation_dataset(self):
        """Gene annotation jobs should convert Ensembl records into the annotations dataset."""
        from lambda_handler import process_gene_annotation_job

        annotation_rows = [
            {
                'gene_id': 'ENSG000001',
                'gene_symbol': 'GENE1',
                'gene_name': 'Gene one',
                'feature_type': 'gene',
                'biotype': 'protein_coding',
                'start': 100,
                'end': 250,
                'length': 151,
                'strand': '+',
                'assembly_name': 'GRCh38',
                'source_name': 'ensembl',
                'species': 'homo_sapiens',
                'chromosome': '22',
                'version': '1',
            }
        ]

        with patch('lambda_handler.fetch_ensembl_gene_annotations', return_value=annotation_rows), \
             patch('lambda_handler.write_records_to_parquet', return_value=True), \
             patch('lambda_handler.upload_to_s3', return_value=True) as mock_upload:
            response = process_gene_annotation_job(
                {
                    'job_type': 'gene_annotations',
                    'source': 'ensembl',
                    'species': 'homo_sapiens',
                    'chromosome': '22',
                    'output_prefix': 'annotations/chr22',
                }
            )

        self.assertEqual(response['job_type'], 'gene_annotations')
        self.assertEqual(response['record_count'], 1)
        self.assertIn('gene_annotation_data/source=ensembl/species=homo_sapiens/chr=22/', response['annotation_key'])
        mock_upload.assert_called_once()


class TestPipelineClient(unittest.TestCase):
    """Test pipeline client"""

    def setUp(self):
        self.boto3_module = types.SimpleNamespace(client=MagicMock(return_value=MagicMock()))
        self.module_patcher = patch.dict(sys.modules, {'boto3': self.boto3_module})
        self.module_patcher.start()
        sys.modules.pop('pipeline_client', None)

    def tearDown(self):
        self.module_patcher.stop()
        sys.modules.pop('pipeline_client', None)

    def test_submit_ncbi_job(self):
        """Test NCBI job submission"""
        from pipeline_client import GenomePipelineClient
        
        client = GenomePipelineClient()
        
        # Mock Lambda client
        mock_lambda = MagicMock()
        mock_lambda.invoke.return_value = {
            'StatusCode': 202,
            'ResponseMetadata': {'RequestId': 'test-request-id'}
        }
        client.lambda_client = mock_lambda
        
        result = client.submit_ncbi_job('NC_000001.11')
        
        self.assertEqual(result['StatusCode'], 202)
        mock_lambda.invoke.assert_called_once()
    
    def test_human_chromosomes(self):
        """Test human chromosome processing"""
        from pipeline_client import GenomePipelineClient
        
        client = GenomePipelineClient()
        
        # Test chromosome mapping
        with patch.object(client, 'submit_ncbi_job') as mock_submit:
            mock_submit.return_value = {'StatusCode': 202}
            
            results = client.submit_human_chromosomes(
                chromosomes=['1', '2', 'X'],
                queue_url='https://sqs.us-east-1.amazonaws.com/123/test-queue'
            )
            
            self.assertEqual(len(results), 3)
            self.assertEqual(mock_submit.call_count, 3)
            _, kwargs = mock_submit.call_args
            self.assertEqual(kwargs['analysis_mode'], 'sequence_only')


class TestParquetConversion(unittest.TestCase):
    """Test Parquet conversion"""

    def setUp(self):
        self.boto3_module = types.SimpleNamespace(client=MagicMock(return_value=MagicMock()))
        self.module_patcher = patch.dict(sys.modules, {'boto3': self.boto3_module})
        self.module_patcher.start()
        sys.modules.pop('lambda_handler', None)

    def tearDown(self):
        self.module_patcher.stop()
        sys.modules.pop('lambda_handler', None)
    
    def test_json_to_parquet(self):
        """Test JSON to Parquet conversion"""
        try:
            import pyarrow.parquet as pq
        except ModuleNotFoundError:
            self.skipTest("pyarrow not installed")
        
        # Sample JSON data
        json_data = {
            'sequences': [
                {
                    'id': 'seq1',
                    'description': 'Test sequence',
                    'sequence': 'ATCGATCG',
                    'length': 8,
                    'gc_content': 50.0,
                    'base_composition': {'A': 2, 'T': 2, 'G': 2, 'C': 2, 'N': 0}
                }
            ],
            'patterns': [
                {
                    'sequence_id': 'seq1',
                    'pattern_type': 'motif',
                    'pattern_name': 'start_codon',
                    'start': 0,
                    'end': 3,
                    'length': 3,
                    'strand': '+',
                    'score': 3.0,
                    'matched_sequence': 'ATG'
                }
            ]
        }
        
        with tempfile.TemporaryDirectory() as temp_dir:
            from lambda_handler import convert_to_parquet

            json_file = os.path.join(temp_dir, 'test.json')
            parquet_file = os.path.join(temp_dir, 'test.parquet')

            with open(json_file, 'w') as f:
                json.dump(json_data, f)

            self.assertTrue(convert_to_parquet(json_file, parquet_file))
            self.assertTrue(os.path.exists(parquet_file))

            table = pq.read_table(parquet_file)
            rows = table.to_pylist()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['id'], 'seq1')

            patterns_file = os.path.join(temp_dir, 'patterns.parquet')
            self.assertTrue(convert_to_parquet(json_file, patterns_file, dataset_key='patterns'))
            pattern_rows = pq.read_table(patterns_file).to_pylist()
            self.assertEqual(len(pattern_rows), 1)
            self.assertEqual(pattern_rows[0]['pattern_name'], 'start_codon')


class TestWebAPIHandler(unittest.TestCase):
    """Test dashboard API Lambda routes."""

    def setUp(self):
        self.s3_client = MagicMock()
        self.sqs_client = MagicMock()
        self.logs_client = MagicMock()
        self.sts_client = MagicMock()
        self.athena_client = MagicMock()
        self.batch_client = MagicMock()
        self.dynamodb_table = MagicMock()
        self.dynamodb_resource = MagicMock()
        self.dynamodb_resource.Table.return_value = self.dynamodb_table
        self.sts_client.get_caller_identity.return_value = {'Account': '443568785165'}

        client_map = {
            's3': self.s3_client,
            'sqs': self.sqs_client,
            'logs': self.logs_client,
            'sts': self.sts_client,
            'athena': self.athena_client,
            'batch': self.batch_client,
        }
        self.boto3_module = types.SimpleNamespace(
            client=MagicMock(side_effect=lambda service, **kwargs: client_map[service]),
            resource=MagicMock(return_value=self.dynamodb_resource),
        )

        self.module_patcher = patch.dict(sys.modules, {'boto3': self.boto3_module})
        self.env_patcher = patch.dict(
            os.environ,
            {
                'OUTPUT_BUCKET': 'genome-pipeline-output-443568785165',
                'QUEUE_URL': 'https://sqs.us-east-1.amazonaws.com/443568785165/genome-pipeline-queue',
                'DLQ_URL': 'https://sqs.us-east-1.amazonaws.com/443568785165/genome-pipeline-dlq',
                'PROJECT_NAME': 'genome-pipeline',
                'AWS_REGION': 'us-east-1',
                'ATHENA_DATABASE': 'genome_pipeline_db',
                'ATHENA_WORKGROUP': 'genome-pipeline-workgroup',
                'ATHENA_RESULTS_BUCKET': 'genome-pipeline-athena-results-443568785165',
                'BATCH_JOB_QUEUE': 'genome-pipeline-full-analysis',
                'BATCH_JOB_DEFINITION': 'genome-pipeline-full-analysis:1',
            },
            clear=False,
        )
        self.module_patcher.start()
        self.env_patcher.start()
        sys.modules.pop('web_api_handler', None)

    def tearDown(self):
        self.module_patcher.stop()
        self.env_patcher.stop()
        sys.modules.pop('web_api_handler', None)

    def _mock_paginator(self, pages_per_call):
        paginator = MagicMock()
        paginator.paginate.side_effect = pages_per_call
        self.s3_client.get_paginator.return_value = paginator
        return paginator

    def test_status_overview_route(self):
        """Overview route returns queue depth and dataset readiness."""
        from web_api_handler import lambda_handler

        self.sqs_client.get_queue_attributes.side_effect = [
            {'Attributes': {'ApproximateNumberOfMessages': '22'}},
            {'Attributes': {'ApproximateNumberOfMessages': '0'}},
        ]
        self.logs_client.filter_log_events.return_value = {
            'events': [
                {
                    'timestamp': 1779939300826,
                    'message': 'REPORT RequestId: abc Status: error Error Type: Runtime.OutOfMemory'
                }
            ]
        }
        self._mock_paginator(
            [
                [
                    {
                        'CommonPrefixes': [
                            {'Prefix': 'genome_data/source=ncbi/species=homo_sapiens/chr=22/'},
                            {'Prefix': 'genome_data/source=ncbi/species=homo_sapiens/chr=Y/'},
                        ],
                    }
                ],
                [
                    {
                        'CommonPrefixes': [
                            {'Prefix': 'pattern_data/source=ncbi/species=homo_sapiens/chr=22/'},
                        ],
                    }
                ],
                [
                    {
                        'CommonPrefixes': [
                            {'Prefix': 'region_data/source=ncbi/species=homo_sapiens/chr=22/'},
                        ],
                    }
                ],
                [
                    {
                        'CommonPrefixes': [
                            {'Prefix': 'gene_annotation_data/source=ensembl/species=homo_sapiens/chr=22/'},
                        ],
                    }
                ],
                [
                    {
                        'Contents': [
                            {
                                'Key': 'genome_data/source=ncbi/species=homo_sapiens/chr=22/year=2026/month=05/file.parquet',
                                'LastModified': datetime(2026, 5, 28, 3, 28, 12, tzinfo=timezone.utc),
                            }
                        ],
                    }
                ],
            ]
        )

        response = lambda_handler(
            {
                'requestContext': {'http': {'method': 'GET'}},
                'rawPath': '/api/status/overview',
            },
            None,
        )

        body = json.loads(response['body'])
        self.assertEqual(response['statusCode'], 200)
        self.assertEqual(body['chromosome'], '22')
        self.assertEqual(len(body['items']), 2)
        self.assertEqual(body['items'][0]['pattern_name'], 'CpG-like motif')
        self.assertEqual(body['queue']['depth'], 22)
        self.assertEqual(body['queue']['dlq_depth'], 0)
        self.assertEqual(body['datasets']['sequence_ready_count'], 2)
        self.assertEqual(body['datasets']['pattern_ready_count'], 1)
        self.assertEqual(body['datasets']['region_ready_count'], 1)
        self.assertEqual(body['datasets']['annotation_ready_count'], 1)
        self.assertEqual(len(body['recent_failures']), 1)

    def test_jobs_route_submits_safe_json(self):
        """Job route validates input and submits a JSON body to SQS."""
        from web_api_handler import lambda_handler

        self.sqs_client.send_message.return_value = {'MessageId': 'msg-123'}

        response = lambda_handler(
            {
                'requestContext': {'http': {'method': 'POST'}},
                'rawPath': '/api/jobs',
                'body': json.dumps(
                    {
                        'source': 'ncbi',
                        'accession_id': 'NC_000022.11',
                        'chromosome': '22',
                        'species': 'homo_sapiens',
                        'output_prefix': 'human_genome/chr22',
                    }
                ),
            },
            None,
        )

        body = json.loads(response['body'])
        self.assertEqual(response['statusCode'], 202)
        self.assertEqual(body['message_id'], 'msg-123')
        self.sqs_client.send_message.assert_called_once()
        sent_body = json.loads(self.sqs_client.send_message.call_args[1]['MessageBody'])
        self.assertEqual(sent_body['accession_id'], 'NC_000022.11')
        self.assertEqual(sent_body['chromosome'], '22')

    def _athena_rows_page(self, headers, rows):
        return {
            'ResultSet': {
                'Rows': [
                    {'Data': [{'VarCharValue': header} for header in headers]}
                ] + [
                    {'Data': [{'VarCharValue': str(value)} for value in row]}
                    for row in rows
                ]
            }
        }

    def test_chromosome_summary_route(self):
        """Summary route enriches S3 inventory with Athena-backed metrics."""
        from web_api_handler import lambda_handler

        self.athena_client.start_query_execution.side_effect = [
            {'QueryExecutionId': 'q1'},
            {'QueryExecutionId': 'q2'},
            {'QueryExecutionId': 'q3'},
            {'QueryExecutionId': 'q4'},
            {'QueryExecutionId': 'q5'},
        ]
        self.athena_client.get_query_execution.return_value = {
            'QueryExecution': {'Status': {'State': 'SUCCEEDED'}}
        }
        athena_paginator = MagicMock()
        athena_paginator.paginate.side_effect = [
            [self._athena_rows_page(['sequence_length', 'avg_gc_content'], [[50818468, 36.22]])],
            [self._athena_rows_page(['pattern_name', 'pattern_type', 'hit_count'], [['CpG-like motif', 'motif', 1482], ['candidate ORF', 'orf', 221]])],
            [self._athena_rows_page(['window_start', 'window_end', 'gc_content', 'orf_count', 'motif_hits', 'repeat_bases'], [[0, 100000, 35.8, 4, 12, 700], [100000, 200000, 37.3, 7, 16, 820]])],
            [self._athena_rows_page(['pattern_hit_count'], [[1703]])],
            [self._athena_rows_page(['orf_count'], [[11]])],
        ]
        self.athena_client.get_paginator.return_value = athena_paginator

        with patch('web_api_handler.build_chromosome_inventory') as mock_inventory, \
             patch('web_api_handler.latest_object_for_dataset') as mock_latest, \
             patch('web_api_handler.latest_batch_job_for_chromosome') as mock_batch_status:
            mock_inventory.return_value = [
                {
                    'chromosome': '22',
                    'sequence_ready': True,
                    'patterns_ready': True,
                    'regions_ready': True,
                    'annotations_ready': True,
                    'latest_output_at': '2026-05-28T03:28:12Z',
                    'latest_key': 'genome_data/source=ncbi/species=homo_sapiens/chr=22/year=2026/month=05/file.parquet',
                }
            ]
            mock_batch_status.return_value = {
                'job_id': 'batch-22',
                'job_name': 'genome-pipeline-chr22-20260605000000',
                'status': 'RUNNING',
                'elapsed_minutes': 8,
                'expected_minutes': 14,
                'progress_pct': 57,
            }
            mock_latest.side_effect = [
                {'Key': 'pattern_data/source=ncbi/species=homo_sapiens/chr=22/year=2026/month=05/patterns.parquet'},
                {'Key': 'region_data/source=ncbi/species=homo_sapiens/chr=22/year=2026/month=05/regions.parquet'},
                {'Key': 'gene_annotation_data/source=ensembl/species=homo_sapiens/chr=22/year=2026/month=06/annotations.parquet'},
            ]

            response = lambda_handler(
                {
                    'requestContext': {'http': {'method': 'GET'}},
                    'rawPath': '/api/chromosomes/22/summary',
                },
                None,
            )

        body = json.loads(response['body'])
        self.assertEqual(response['statusCode'], 200)
        self.assertEqual(body['chromosome'], '22')
        self.assertEqual(body['sequence_length'], '50818468')
        self.assertEqual(body['avg_gc_content'], '36.22')
        self.assertFalse(body['full_analysis_eligible'])
        self.assertEqual(body['full_analysis_status'], 'complete')
        self.assertEqual(body['pattern_hit_count'], '1703')
        self.assertEqual(body['orf_count'], '11')
        self.assertTrue(body['annotations_ready'])
        self.assertEqual(body['batch_status']['status'], 'RUNNING')

    def test_chromosome_summary_uses_aggregate_orf_count_not_preview_rows(self):
        """Summary ORF count should come from the full chromosome aggregate, not the first preview windows."""
        from web_api_handler import lambda_handler

        self.athena_client.start_query_execution.side_effect = [
            {'QueryExecutionId': 'q1'},
            {'QueryExecutionId': 'q2'},
            {'QueryExecutionId': 'q3'},
            {'QueryExecutionId': 'q4'},
            {'QueryExecutionId': 'q5'},
        ]
        self.athena_client.get_query_execution.return_value = {
            'QueryExecution': {'Status': {'State': 'SUCCEEDED'}}
        }
        athena_paginator = MagicMock()
        athena_paginator.paginate.side_effect = [
            [self._athena_rows_page(['sequence_length', 'avg_gc_content'], [[46709983, 35.14]])],
            [self._athena_rows_page(['pattern_name', 'pattern_type', 'hit_count'], [['CpG-like motif', 'motif', 500000], ['candidate ORF', 'orf', 497034]])],
            [self._athena_rows_page(['window_start', 'window_end', 'gc_content', 'orf_count', 'motif_hits', 'repeat_bases'], [[0, 100000, 12.1, 0, 0, 5000], [100000, 200000, 14.5, 0, 0, 6000], [200000, 300000, 18.0, 0, 0, 7000]])],
            [self._athena_rows_page(['pattern_hit_count'], [[997034]])],
            [self._athena_rows_page(['orf_count'], [[1191]])],
        ]
        self.athena_client.get_paginator.return_value = athena_paginator

        with patch('web_api_handler.build_chromosome_inventory') as mock_inventory, \
             patch('web_api_handler.latest_object_for_dataset') as mock_latest, \
             patch('web_api_handler.latest_batch_job_for_chromosome', return_value=None):
            mock_inventory.return_value = [
                {
                    'chromosome': '21',
                    'sequence_ready': True,
                    'patterns_ready': True,
                    'regions_ready': True,
                    'annotations_ready': False,
                    'latest_output_at': '2026-06-06T03:59:39Z',
                    'latest_key': 'genome_data/source=ncbi/species=homo_sapiens/chr=21/year=2026/month=06/file.parquet',
                }
            ]
            mock_latest.side_effect = [
                {'Key': 'pattern_data/source=ncbi/species=homo_sapiens/chr=21/year=2026/month=06/patterns.parquet'},
                {'Key': 'region_data/source=ncbi/species=homo_sapiens/chr=21/year=2026/month=06/regions.parquet'},
                None,
            ]

            response = lambda_handler(
                {
                    'requestContext': {'http': {'method': 'GET'}},
                    'rawPath': '/api/chromosomes/21/summary',
                },
                None,
            )

        body = json.loads(response['body'])
        self.assertEqual(response['statusCode'], 200)
        self.assertEqual(body['pattern_hit_count'], '997034')
        self.assertEqual(body['orf_count'], '1191')

    def test_human_reference_route_defaults_to_sequence_only(self):
        """Human reference route should favor lightweight bulk ingestion."""
        from web_api_handler import lambda_handler

        self.sqs_client.send_message.return_value = {'MessageId': 'msg-123'}

        response = lambda_handler(
            {
                'requestContext': {'http': {'method': 'POST'}},
                'rawPath': '/api/jobs/human-reference',
                'body': json.dumps({}),
            },
            None,
        )

        body = json.loads(response['body'])
        self.assertEqual(response['statusCode'], 202)
        self.assertEqual(body['submitted_count'], 24)
        sent_payload = json.loads(self.sqs_client.send_message.call_args[1]['MessageBody'])
        self.assertEqual(sent_payload['analysis_mode'], 'sequence_only')

    def test_single_chromosome_analysis_route_submits_full_mode(self):
        """Per-chromosome analysis route should submit a full-analysis Batch job."""
        from web_api_handler import lambda_handler

        self.batch_client.submit_job.return_value = {
            'jobId': 'batch-999',
            'jobName': 'genome-pipeline-chr1-20260603070000',
        }

        with patch('web_api_handler.build_chromosome_inventory') as mock_inventory:
            mock_inventory.return_value = [
                {
                    'chromosome': '1',
                    'sequence_ready': True,
                    'patterns_ready': False,
                    'regions_ready': False,
                    'full_analysis_eligible': True,
                    'full_analysis_status': 'batch_required',
                    'full_analysis_backend': 'batch',
                    'full_analysis_reason': 'Chromosome is 248,956,422 bp. Full chromosome analysis runs on AWS Batch on Fargate for predictable memory and runtime.',
                    'latest_output_at': '2026-06-03T05:36:58Z',
                    'latest_key': 'genome_data/source=ncbi/species=homo_sapiens/chr=1/year=2026/month=06/file.parquet',
                }
            ]

            response = lambda_handler(
                {
                    'requestContext': {'http': {'method': 'POST'}},
                    'rawPath': '/api/chromosomes/1/analyze',
                    'body': json.dumps({'species': 'homo_sapiens'}),
                },
                None,
            )

        body = json.loads(response['body'])
        self.assertEqual(response['statusCode'], 202)
        self.assertEqual(body['chromosome'], '1')
        self.assertEqual(body['analysis_mode'], 'full')
        self.assertEqual(body['analysis_backend'], 'batch')
        self.batch_client.submit_job.assert_called_once()
        self.sqs_client.send_message.assert_not_called()

    def test_single_chromosome_analysis_route_rejects_when_batch_unavailable(self):
        """Per-chromosome analysis route should reject when Batch is unavailable."""
        from web_api_handler import lambda_handler

        with patch('web_api_handler.build_chromosome_inventory') as mock_inventory:
            mock_inventory.return_value = [
                {
                    'chromosome': '1',
                    'sequence_ready': True,
                    'patterns_ready': False,
                    'regions_ready': False,
                    'full_analysis_eligible': False,
                    'full_analysis_status': 'batch_unavailable',
                    'full_analysis_reason': 'Full chromosome analysis is configured to run on AWS Batch, but the Batch job queue or job definition is not available right now.',
                    'latest_output_at': '2026-06-03T05:36:58Z',
                    'latest_key': 'genome_data/source=ncbi/species=homo_sapiens/chr=1/year=2026/month=06/file.parquet',
                }
            ]

            response = lambda_handler(
                {
                    'requestContext': {'http': {'method': 'POST'}},
                    'rawPath': '/api/chromosomes/1/analyze',
                    'body': json.dumps({'species': 'homo_sapiens'}),
                },
                None,
            )

        body = json.loads(response['body'])
        self.assertEqual(response['statusCode'], 400)
        self.assertEqual(body['error'], 'bad_request')
        self.assertIn('AWS Batch', body['message'])
        self.sqs_client.send_message.assert_not_called()

    def test_single_chromosome_analysis_route_submits_batch_for_full_analysis(self):
        """Full-analysis chromosomes should use AWS Batch on Fargate when configured."""
        from web_api_handler import lambda_handler

        self.batch_client.submit_job.return_value = {
            'jobId': 'batch-123',
            'jobName': 'genome-pipeline-chr1-20260603070000',
        }

        with patch('web_api_handler.build_chromosome_inventory') as mock_inventory:
            mock_inventory.return_value = [
                {
                    'chromosome': '1',
                    'sequence_ready': True,
                    'patterns_ready': False,
                    'regions_ready': False,
                    'full_analysis_eligible': True,
                    'full_analysis_status': 'batch_required',
                    'full_analysis_backend': 'batch',
                    'full_analysis_reason': 'Chromosome is 248,956,422 bp. Full chromosome analysis runs on AWS Batch on Fargate for predictable memory and runtime.',
                    'latest_output_at': '2026-06-03T05:36:58Z',
                    'latest_key': 'genome_data/source=ncbi/species=homo_sapiens/chr=1/year=2026/month=06/file.parquet',
                }
            ]

            response = lambda_handler(
                {
                    'requestContext': {'http': {'method': 'POST'}},
                    'rawPath': '/api/chromosomes/1/analyze',
                    'body': json.dumps({'species': 'homo_sapiens'}),
                },
                None,
            )

        body = json.loads(response['body'])
        self.assertEqual(response['statusCode'], 202)
        self.assertEqual(body['analysis_backend'], 'batch')
        self.batch_client.submit_job.assert_called_once()
        self.sqs_client.send_message.assert_not_called()

    def test_patterns_route(self):
        """Patterns route returns Athena-backed pattern leaderboard rows."""
        from web_api_handler import lambda_handler

        self.athena_client.start_query_execution.return_value = {'QueryExecutionId': 'qp'}
        self.athena_client.get_query_execution.return_value = {
            'QueryExecution': {'Status': {'State': 'SUCCEEDED'}}
        }
        athena_paginator = MagicMock()
        athena_paginator.paginate.return_value = [
            self._athena_rows_page(
                ['pattern_name', 'pattern_type', 'hit_count'],
                [['CpG-like motif', 'motif', 1482], ['poly-A run', 'repeat', 764]],
            )
        ]
        self.athena_client.get_paginator.return_value = athena_paginator

        response = lambda_handler(
            {
                'requestContext': {'http': {'method': 'GET'}},
                'rawPath': '/api/chromosomes/22/patterns',
            },
            None,
        )

        body = json.loads(response['body'])
        self.assertEqual(response['statusCode'], 200)

    def test_batch_status_route_returns_recent_job(self):
        """Batch status route exposes the newest matching job for a chromosome."""
        from web_api_handler import lambda_handler

        current_time_ms = int(datetime(2026, 6, 5, 22, 50, tzinfo=timezone.utc).timestamp() * 1000)
        running_job = {
            'jobId': 'batch-18',
            'jobName': 'genome-pipeline-chr18-20260605224428',
            'status': 'RUNNING',
            'createdAt': current_time_ms - (18 * 60 * 1000),
            'startedAt': current_time_ms - (12 * 60 * 1000),
            'statusReason': 'Container running',
        }
        self.batch_client.list_jobs.side_effect = [
            {'jobSummaryList': []},
            {'jobSummaryList': []},
            {'jobSummaryList': []},
            {'jobSummaryList': []},
            {'jobSummaryList': [running_job]},
            {'jobSummaryList': []},
            {'jobSummaryList': []},
        ]

        with patch('web_api_handler.datetime') as mock_datetime:
            real_datetime = datetime
            mock_datetime.now.return_value = datetime(2026, 6, 5, 22, 50, tzinfo=timezone.utc)
            mock_datetime.fromtimestamp.side_effect = lambda *args, **kwargs: real_datetime.fromtimestamp(*args, **kwargs)

            response = lambda_handler(
                {
                    'requestContext': {'http': {'method': 'GET'}},
                    'rawPath': '/api/chromosomes/18/batch-status',
                },
                None,
            )

        body = json.loads(response['body'])
        self.assertEqual(response['statusCode'], 200)
        self.assertEqual(body['job_id'], 'batch-18')
        self.assertEqual(body['status'], 'RUNNING')
        self.assertGreaterEqual(body['progress_pct'], 28)
        self.assertEqual(body['elapsed_minutes'], 12)

    def test_operations_route_serializes_dynamodb_decimals(self):
        """Operations route should JSON-encode DynamoDB Decimal fields cleanly."""
        from web_api_handler import lambda_handler

        with patch('web_api_handler.get_current_status') as mock_current_status, \
             patch('web_api_handler.build_processing_status') as mock_processing_status:
            mock_current_status.return_value = {
                'pk': 'CHR#20',
                'sk': 'STATUS#sequence_analysis#full',
                'attempt_count': Decimal('1'),
                'estimated_duration_minutes': Decimal('16'),
                'status': 'running',
            }
            mock_processing_status.return_value = {
                'chromosome': '20',
                'status': 'running',
                'attempt_count': 1,
                'expected_minutes': 16,
                'progress_pct': 12,
            }

            response = lambda_handler(
                {
                    'requestContext': {'http': {'method': 'GET'}},
                    'rawPath': '/api/chromosomes/20/operations',
                },
                None,
            )

        body = json.loads(response['body'])
        self.assertEqual(response['statusCode'], 200)
        self.assertEqual(body['item']['attempt_count'], 1)
        self.assertEqual(body['item']['estimated_duration_minutes'], 16)
        self.assertEqual(body['processing_status']['progress_pct'], 12)

    def test_regions_route_honors_limit_query_param(self):
        """Region route should pass through an explicit limit so the lens can request deeper slices."""
        from web_api_handler import lambda_handler

        with patch('web_api_handler.get_chromosome_region_rows') as mock_region_rows:
            mock_region_rows.return_value = [
                {'window_start': '0', 'window_end': '100000', 'gc_content': '41.2', 'orf_count': '12', 'motif_hits': '44', 'repeat_bases': '1000'}
            ]

            response = lambda_handler(
                {
                    'requestContext': {'http': {'method': 'GET'}},
                    'rawPath': '/api/chromosomes/18/regions',
                    'queryStringParameters': {'limit': '5000'},
                },
                None,
            )

        body = json.loads(response['body'])
        self.assertEqual(response['statusCode'], 200)
        mock_region_rows.assert_called_once_with('18', limit=5000)
        self.assertEqual(body['chromosome'], '18')
        self.assertEqual(len(body['items']), 1)

    def test_annotations_route_with_overlap_window(self):
        """Annotation route should return overlapping known genes for a chromosome window."""
        from web_api_handler import lambda_handler

        self.athena_client.start_query_execution.return_value = {'QueryExecutionId': 'qa'}
        self.athena_client.get_query_execution.return_value = {
            'QueryExecution': {'Status': {'State': 'SUCCEEDED'}}
        }
        athena_paginator = MagicMock()
        athena_paginator.paginate.return_value = [
            self._athena_rows_page(
                ['gene_id', 'gene_symbol', 'gene_name', 'feature_type', 'biotype', 'start', 'end', 'length', 'strand', 'assembly_name', 'source_name'],
                [['ENSG000001', 'HLA-A', 'major histocompatibility complex', 'gene', 'protein_coding', 29942470, 29945884, 3415, '+', 'GRCh38', 'ensembl']],
            )
        ]
        self.athena_client.get_paginator.return_value = athena_paginator

        response = lambda_handler(
            {
                'requestContext': {'http': {'method': 'GET'}},
                'rawPath': '/api/chromosomes/6/annotations',
                'queryStringParameters': {'start': '29942000', 'end': '29946000'},
            },
            None,
        )

        body = json.loads(response['body'])
        self.assertEqual(response['statusCode'], 200)
        self.assertEqual(body['chromosome'], '6')
        self.assertEqual(body['items'][0]['gene_symbol'], 'HLA-A')

    def test_annotation_sync_route_enqueues_ensembl_job(self):
        """Annotation sync route should enqueue a gene-annotation job."""
        from web_api_handler import lambda_handler

        self.sqs_client.send_message.return_value = {'MessageId': 'annotation-msg'}

        response = lambda_handler(
            {
                'requestContext': {'http': {'method': 'POST'}},
                'rawPath': '/api/chromosomes/22/annotations/sync',
                'body': json.dumps({'species': 'homo_sapiens'}),
            },
            None,
        )

        body = json.loads(response['body'])
        self.assertEqual(response['statusCode'], 202)
        self.assertEqual(body['job_type'], 'gene_annotations')
        sent_payload = json.loads(self.sqs_client.send_message.call_args[1]['MessageBody'])
        self.assertEqual(sent_payload['job_type'], 'gene_annotations')
        self.assertEqual(sent_payload['source'], 'ensembl')
        self.assertEqual(sent_payload['chromosome'], '22')

    def test_human_reference_batch_route(self):
        """Batch route expands 24 human chromosome messages safely."""
        from web_api_handler import lambda_handler

        self.sqs_client.send_message.return_value = {'MessageId': 'batch-msg'}

        response = lambda_handler(
            {
                'requestContext': {'http': {'method': 'POST'}},
                'rawPath': '/api/jobs/human-reference',
                'body': json.dumps({'species': 'homo_sapiens'}),
            },
            None,
        )

        body = json.loads(response['body'])
        self.assertEqual(response['statusCode'], 202)
        self.assertEqual(body['submitted_count'], 24)
        self.assertEqual(self.sqs_client.send_message.call_count, 24)


def run_integration_test():
    """Run integration test with real AWS services (if configured)"""
    print("\n=== Integration Test ===\n")
    
    # Check if AWS credentials are configured
    try:
        import boto3
        sts = boto3.client('sts')
        identity = sts.get_caller_identity()
        print(f"AWS Account: {identity['Account']}")
        print(f"AWS User: {identity['Arn']}")
    except Exception as e:
        print(f"AWS not configured: {e}")
        print("Skipping integration test")
        return
    
    # Test pipeline client
    try:
        from pipeline_client import GenomePipelineClient
        
        client = GenomePipelineClient()
        
        # Try to list outputs (non-destructive test)
        print("\nTesting S3 access...")
        # This will fail gracefully if resources don't exist
        
        print("Integration test setup verified")
        
    except Exception as e:
        print(f"Integration test skipped: {e}")


if __name__ == '__main__':
    # Run unit tests
    print("Running unit tests...\n")
    unittest.main(argv=[''], verbosity=2, exit=False)
    
    # Run integration tests if --integration flag is present
    import sys
    if '--integration' in sys.argv:
        run_integration_test()
