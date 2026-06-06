# Quick Start Guide - Genome Data Pipeline

Get up and running with the Genome Data Pipeline in 15 minutes!

## Prerequisites Checklist

- [ ] AWS Account with admin access
- [ ] AWS CLI installed and configured
- [ ] Python 3.11+ installed
- [ ] g++ compiler installed
- [ ] Terraform installed
- [ ] 5 GB free disk space

## 5-Minute Local Test

Test the C++ parser locally before deploying to AWS:

```bash
# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Build C++ parser
make build-cpp

# 3. Test with sample data
make test-cpp

# 4. View output
cat test_data/output.json
```

Expected output: JSON with parsed sequence data, GC content, and base composition.

## 15-Minute AWS Deployment

### Step 1: Build Packages (3 minutes)

```bash
# Build everything
make build

# Verify artifacts
ls -lh dist/
```

You should see:
- `lambda_layer.zip` (~50-100 MB)
- `lambda_function.zip` (~5 KB)
- `terraform/` directory

### Step 2: Deploy Infrastructure (5 minutes)

```bash
# Initialize and deploy
make deploy

# Or manually:
cd dist/terraform
terraform init
terraform plan
terraform apply
```

Answer `yes` when prompted.

### Step 3: Get Your Resources (1 minute)

```bash
# Save these values!
make outputs

# Or:
cd dist/terraform
terraform output
```

Note down:
- `output_bucket_name`
- `sqs_queue_url`
- `lambda_function_name`

### Step 4: Test the Pipeline (5 minutes)

```bash
# Test with smallest human chromosome (Chr 22)
python3 << EOF
from pipeline_client import GenomePipelineClient

client = GenomePipelineClient(region='us-east-1')
result = client.submit_ncbi_job(
    accession_id='NC_000022.11',
    output_prefix='chr22'
)
print(f"Job submitted: {result}")
EOF
```

### Step 5: Monitor Execution (1 minute)

```bash
# Watch logs
make logs

# Or:
aws logs tail /aws/lambda/genome-pipeline-processor --follow
```

Wait for completion (~2-5 minutes depending on sequence size).

### Step 6: Check Results

```bash
# List outputs
aws s3 ls s3://YOUR-BUCKET-NAME/genome_data/source=ncbi/species=homo_sapiens/chr=22/ --recursive

# Download result
aws s3 cp s3://YOUR-BUCKET-NAME/genome_data/source=ncbi/species=homo_sapiens/chr=22/year=YYYY/month=MM/chr22_TIMESTAMP.parquet ./chr22.parquet

# View with Python
python3 << EOF
import pandas as pd
df = pd.read_parquet('chr22.parquet')
print(f"Sequences: {len(df)}")
print(f"Columns: {df.columns.tolist()}")
print(df.head())
EOF
```

## Common Issues & Solutions

### Build fails with "g++ not found"

```bash
# Ubuntu/Debian
sudo apt-get install build-essential

# macOS
xcode-select --install
```

### Terraform apply fails with permissions error

Check your AWS credentials:
```bash
aws sts get-caller-identity
```

Ensure your user has these permissions:
- IAM (create roles/policies)
- Lambda (create functions/layers)
- S3 (create buckets)
- SQS (create queues)
- Step Functions (create state machines)

### Lambda timeout or out of memory

Edit `dist/terraform/main.tf`:
```hcl
variable "lambda_timeout" {
  default     = 900  # Increase to 900 (15 min)
}

variable "lambda_memory" {
  default     = 3008  # Increase to maximum
}
```

Then:
```bash
cd dist/terraform
terraform apply
```

### "Cannot download from NCBI"

Check Lambda environment variable:
```bash
aws lambda get-function-configuration \
  --function-name genome-pipeline-processor \
  --query 'Environment.Variables.NCBI_EMAIL'
```

Update if needed:
```bash
aws lambda update-function-configuration \
  --function-name genome-pipeline-processor \
  --environment Variables={NCBI_EMAIL=your.email@example.com}
```

## Next Steps

### Process Full Human Genome

```python
from pipeline_client import GenomePipelineClient

client = GenomePipelineClient(region='us-east-1')

# Get queue URL from terraform output
queue_url = 'YOUR_QUEUE_URL'

# Process all chromosomes
results = client.submit_human_chromosomes(queue_url=queue_url)
print(f"Submitted {len(results)} jobs")
```

**Note**: Processing all 24 chromosomes will take several hours and cost approximately $5-10.

### Safe Queue Submission Note

If you submit the full chromosome set outside the Python client, do not hand-build JSON inside shell loops.

Use one of these safer options instead:

1. Use `GenomePipelineClient.submit_human_chromosomes(queue_url=queue_url)`.
2. Use a Python script with `json.dumps(...)` for `aws sqs send-message`.
3. Use `--message-body file://payload.json` with the AWS CLI.

Malformed JSON can still be accepted by SQS, but then fail in Lambda and eventually end up in the DLQ.

### Set Up Monitoring

Create CloudWatch dashboard:
```bash
aws cloudwatch put-dashboard \
  --dashboard-name GenomePipeline \
  --dashboard-body file://monitoring-dashboard.json
```

### Schedule Regular Processing

Edit `dist/terraform/main.tf` and enable the EventBridge rule:
```hcl
resource "aws_cloudwatch_event_rule" "daily_processing" {
  # ... existing config ...
  is_enabled = true  # Change from false to true
}
```

### Optimize Costs

1. **Use Spot Instances for EC2 alternatives** (if you migrate from Lambda)
2. **Set S3 lifecycle policies**:
   ```bash
   aws s3api put-bucket-lifecycle-configuration \
     --bucket YOUR-BUCKET \
     --lifecycle-configuration file://lifecycle.json
   ```
3. **Monitor with AWS Cost Explorer**
4. **Set up billing alerts**

## Clean Up

To avoid ongoing charges:

```bash
# Delete all data
aws s3 rm s3://YOUR-BUCKET-NAME --recursive

# Destroy infrastructure
make terraform-destroy

# Or manually:
cd dist/terraform
terraform destroy
```

Type `yes` when prompted.

## Getting Help

1. **Check logs**: `make logs`
2. **Review README**: `cat README.md`
3. **Run examples**: `python examples.py`
4. **Check AWS CloudWatch** for Lambda metrics
5. **Open GitHub issue** for bugs

## What's Next?

- Read the full [README.md](README.md) for detailed documentation
- Explore [examples.py](examples.py) for more usage patterns
- Check [test_pipeline.py](test_pipeline.py) for testing approaches
- Customize Terraform in `terraform/main.tf` for your needs

## Estimated Costs

**Per chromosome processed**:
- Lambda execution: $0.10 - $0.50
- S3 storage: $0.02/GB/month
- Data transfer: Minimal (NCBI is free)

**Full human genome (24 chromosomes)**:
- One-time processing: $5 - $10
- Monthly storage (all chromosomes): $2 - $5

**Tips to minimize costs**:
1. Test with Chr 21 or 22 first (smallest)
2. Delete intermediate files
3. Use S3 lifecycle policies
4. Process only needed chromosomes

## Success Criteria

You've successfully deployed the pipeline when:

✅ `make build` completes without errors  
✅ `terraform apply` succeeds  
✅ Test job completes successfully  
✅ Parquet file is in S3  
✅ You can read the Parquet file with pandas  

## Support Matrix

| Feature | Status | Notes |
|---------|--------|-------|
| NCBI Download | ✅ | All accession types |
| Ensembl Download | ✅ | GRCh38 |
| FASTA Parsing | ✅ | Full support |
| FASTQ Parsing | ✅ | With quality scores |
| Parquet Output | ✅ | Snappy compression |
| SQS Queue | ✅ | Batch processing |
| Step Functions | ✅ | Orchestration |
| CloudWatch Logs | ✅ | Full logging |
| Multi-region | ⚠️  | Requires manual config |

Ready to process some genomes? 🧬

Run: `python examples.py 1`
