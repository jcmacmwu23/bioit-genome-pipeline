# Makefile for Genome Data Pipeline

.PHONY: all clean build deploy test help

# Variables
PROJECT_NAME := genome-pipeline
AWS_REGION := us-east-1
BUILD_DIR := build
DIST_DIR := dist
TERRAFORM_DIR := $(DIST_DIR)/terraform
UNAME_S := $(shell uname -s)

CPPFLAGS := -O3 -std=c++17
CPP_INCLUDE := -I$(BUILD_DIR)/json
CPP_STATIC_FLAGS := -static-libgcc -static-libstdc++
LAYER_SITE_PACKAGES := $(BUILD_DIR)/layer/python/lib/python3.11/site-packages

ifeq ($(UNAME_S),Darwin)
CPP_STATIC_FLAGS :=
endif

# Colors for output
RED := \033[0;31m
GREEN := \033[0;32m
YELLOW := \033[0;33m
NC := \033[0m # No Color

help: ## Show this help message
	@echo "$(GREEN)Genome Data Pipeline - Makefile Commands$(NC)"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(YELLOW)%-20s$(NC) %s\n", $$1, $$2}'
	@echo ""

all: clean build ## Clean and build everything

clean: ## Clean build artifacts
	@echo "$(YELLOW)Cleaning build artifacts...$(NC)"
	rm -rf $(BUILD_DIR) $(DIST_DIR)
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@echo "$(GREEN)Clean complete$(NC)"

install-deps: ## Install Python dependencies locally
	@echo "$(YELLOW)Installing Python dependencies...$(NC)"
	python3 -m pip install -r requirements.txt
	@echo "$(GREEN)Dependencies installed$(NC)"

build-cpp: ## Build C++ parser
	@echo "$(YELLOW)Building C++ FASTA/FASTQ parser...$(NC)"
	mkdir -p $(BUILD_DIR)/json/nlohmann
	@if [ ! -f $(BUILD_DIR)/json/nlohmann/json.hpp ]; then \
		if [ -f build/nlohmann/json.hpp ]; then \
			echo "$(YELLOW)Reusing local nlohmann/json header...$(NC)"; \
			cp build/nlohmann/json.hpp $(BUILD_DIR)/json/nlohmann/json.hpp; \
		else \
			echo "$(YELLOW)Downloading nlohmann/json library...$(NC)"; \
			if command -v wget >/dev/null 2>&1; then \
				wget -q https://github.com/nlohmann/json/releases/download/v3.11.3/json.hpp \
					-O $(BUILD_DIR)/json/nlohmann/json.hpp; \
			elif command -v curl >/dev/null 2>&1; then \
				curl -fsSL https://github.com/nlohmann/json/releases/download/v3.11.3/json.hpp \
					-o $(BUILD_DIR)/json/nlohmann/json.hpp; \
			else \
				echo "$(RED)Neither wget nor curl is installed$(NC)"; \
				exit 1; \
			fi; \
		fi; \
		if [ ! -f $(BUILD_DIR)/json/nlohmann/json.hpp ]; then \
			echo "$(RED)Unable to prepare nlohmann/json header$(NC)"; \
			exit 1; \
		fi; \
	fi
	g++ $(CPPFLAGS) \
		$(CPP_INCLUDE) \
		$(CPP_STATIC_FLAGS) \
		fasta_parser.cpp \
		-o $(BUILD_DIR)/fasta_parser
	@echo "$(GREEN)C++ parser built successfully$(NC)"

validate-lambda-binary: ## Ensure the packaged parser binary is Linux-compatible for Lambda
	@echo "$(YELLOW)Validating Lambda parser binary format...$(NC)"
	@file "$(BUILD_DIR)/fasta_parser" | grep -q "ELF" || \
		( echo "$(RED)build/fasta_parser is not a Linux ELF binary. Rebuild the parser in a Lambda-compatible Linux environment before deploying.$(NC)"; exit 1 )
	@echo "$(GREEN)Lambda parser binary is Linux-compatible$(NC)"

test-cpp: build-cpp ## Test C++ parser with sample data
	@echo "$(YELLOW)Testing C++ parser...$(NC)"
	@if [ ! -d test_data ]; then \
		mkdir -p test_data; \
		echo ">test_sequence_1" > test_data/sample.fasta; \
		echo "ATCGATCGATCGATCG" >> test_data/sample.fasta; \
		echo ">test_sequence_2" >> test_data/sample.fasta; \
		echo "GCTAGCTAGCTAGCTA" >> test_data/sample.fasta; \
	fi
	$(BUILD_DIR)/fasta_parser test_data/sample.fasta test_data/output.json
	@echo "$(GREEN)Parser test complete. Output: test_data/output.json$(NC)"

build-layer: build-cpp validate-lambda-binary ## Build Lambda layer
	@echo "$(YELLOW)Building Lambda layer...$(NC)"
	rm -rf $(BUILD_DIR)/layer
	mkdir -p $(LAYER_SITE_PACKAGES)
	mkdir -p $(BUILD_DIR)/layer/bin
	cp $(BUILD_DIR)/fasta_parser $(BUILD_DIR)/layer/bin/
	chmod +x $(BUILD_DIR)/layer/bin/fasta_parser
	python3 -m pip install -r requirements.txt \
		--target $(LAYER_SITE_PACKAGES) \
		--platform manylinux2014_x86_64 \
		--only-binary=:all: \
		--upgrade \
		--quiet
	find $(LAYER_SITE_PACKAGES) -type d \( -name tests -o -name test -o -name __pycache__ -o -name include -o -name src \) -prune -exec rm -rf {} +
	find $(LAYER_SITE_PACKAGES) -type d -name "*.dist-info" -prune -exec rm -rf {} +
	find $(LAYER_SITE_PACKAGES) -type f \( -name "*.pyc" -o -name "*.pyo" -o -name "*.pxd" -o -name "*.pyi" \) -delete
	mkdir -p $(DIST_DIR)
	cd $(BUILD_DIR)/layer && zip -r ../../$(DIST_DIR)/lambda_layer.zip . -q
	@echo "$(GREEN)Lambda layer built: $(DIST_DIR)/lambda_layer.zip ($$(du -h $(DIST_DIR)/lambda_layer.zip | cut -f1))$(NC)"

build-function: ## Build Lambda function package
	@echo "$(YELLOW)Building Lambda function...$(NC)"
	mkdir -p $(BUILD_DIR)/function
	cp lambda_handler.py $(BUILD_DIR)/function/
	cp operations_store.py $(BUILD_DIR)/function/
	mkdir -p $(DIST_DIR)
	cd $(BUILD_DIR)/function && zip -r ../../$(DIST_DIR)/lambda_function.zip . -q
	@echo "$(GREEN)Lambda function built: $(DIST_DIR)/lambda_function.zip ($$(du -h $(DIST_DIR)/lambda_function.zip | cut -f1))$(NC)"

build-web-api: ## Build dashboard API Lambda package
	@echo "$(YELLOW)Building dashboard API Lambda function...$(NC)"
	rm -rf $(BUILD_DIR)/web_api
	mkdir -p $(BUILD_DIR)/web_api
	cp web_api_handler.py $(BUILD_DIR)/web_api/
	cp operations_store.py $(BUILD_DIR)/web_api/
	mkdir -p $(DIST_DIR)
	cd $(BUILD_DIR)/web_api && zip -r ../../$(DIST_DIR)/web_api_function.zip . -q
	@echo "$(GREEN)Web API function built: $(DIST_DIR)/web_api_function.zip ($$(du -h $(DIST_DIR)/web_api_function.zip | cut -f1))$(NC)"

build: build-layer build-function build-web-api ## Build all deployment packages
	@echo "$(YELLOW)Preparing Terraform configuration...$(NC)"
	mkdir -p $(TERRAFORM_DIR)
	cp main.tf $(TERRAFORM_DIR)/
	cp $(DIST_DIR)/lambda_layer.zip $(TERRAFORM_DIR)/
	cp $(DIST_DIR)/lambda_function.zip $(TERRAFORM_DIR)/
	cp $(DIST_DIR)/web_api_function.zip $(TERRAFORM_DIR)/
	rm -rf $(TERRAFORM_DIR)/webapp
	cp -R webapp $(TERRAFORM_DIR)/
	@echo "$(GREEN)Build complete!$(NC)"
	@echo ""
	@echo "Artifacts created in $(DIST_DIR):"
	@echo "  - lambda_layer.zip    ($$(du -h $(DIST_DIR)/lambda_layer.zip | cut -f1))"
	@echo "  - lambda_function.zip ($$(du -h $(DIST_DIR)/lambda_function.zip | cut -f1))"
	@echo "  - web_api_function.zip ($$(du -h $(DIST_DIR)/web_api_function.zip | cut -f1))"
	@echo "  - terraform/          (Infrastructure as Code)"

terraform-init: build ## Initialize Terraform
	@echo "$(YELLOW)Initializing Terraform...$(NC)"
	cd $(TERRAFORM_DIR) && terraform init
	@echo "$(GREEN)Terraform initialized$(NC)"

terraform-plan: terraform-init ## Run Terraform plan
	@echo "$(YELLOW)Planning Terraform deployment...$(NC)"
	cd $(TERRAFORM_DIR) && terraform plan
	@echo "$(GREEN)Terraform plan complete$(NC)"

terraform-apply: terraform-init ## Deploy infrastructure with Terraform
	@echo "$(YELLOW)Deploying infrastructure...$(NC)"
	cd $(TERRAFORM_DIR) && terraform apply
	@echo "$(GREEN)Deployment complete!$(NC)"
	@echo ""
	@echo "$(YELLOW)Resource Outputs:$(NC)"
	cd $(TERRAFORM_DIR) && terraform output

terraform-destroy: ## Destroy infrastructure
	@echo "$(RED)WARNING: This will destroy all infrastructure!$(NC)"
	@echo "Press Ctrl+C to cancel, or Enter to continue..."
	@read confirm
	cd $(TERRAFORM_DIR) && terraform destroy
	@echo "$(GREEN)Infrastructure destroyed$(NC)"

deploy: build terraform-apply ## Build and deploy everything

test-local: ## Test Lambda handler locally
	@echo "$(YELLOW)Testing Lambda handler locally...$(NC)"
	python -c "from lambda_handler import lambda_handler; \
		import json; \
		event = {'source': 'ncbi', 'accession_id': 'NC_000022.11', 'output_prefix': 'test/chr22'}; \
		print('Event:', json.dumps(event, indent=2)); \
		result = lambda_handler(event, None); \
		print('Result:', json.dumps(result, indent=2))"

test-invoke: ## Invoke deployed Lambda function
	@echo "$(YELLOW)Invoking Lambda function...$(NC)"
	aws lambda invoke \
		--function-name $(PROJECT_NAME)-processor \
		--payload '{"source":"ncbi","accession_id":"NC_000022.11","output_prefix":"test/chr22"}' \
		--region $(AWS_REGION) \
		response.json
	@echo "$(GREEN)Response:$(NC)"
	@cat response.json | python -m json.tool
	@rm -f response.json

logs: ## Tail Lambda logs
	@echo "$(YELLOW)Tailing Lambda logs...$(NC)"
	aws logs tail /aws/lambda/$(PROJECT_NAME)-processor --follow --region $(AWS_REGION)

outputs: ## Show Terraform outputs
	@echo "$(YELLOW)Terraform Outputs:$(NC)"
	cd $(TERRAFORM_DIR) && terraform output

format-terraform: ## Format Terraform files
	@echo "$(YELLOW)Formatting Terraform files...$(NC)"
	cd terraform && terraform fmt -recursive
	@echo "$(GREEN)Terraform files formatted$(NC)"

validate-terraform: ## Validate Terraform configuration
	@echo "$(YELLOW)Validating Terraform...$(NC)"
	cd $(TERRAFORM_DIR) && terraform validate
	@echo "$(GREEN)Terraform configuration valid$(NC)"

estimate-costs: ## Estimate AWS costs (requires infracost)
	@echo "$(YELLOW)Estimating costs...$(NC)"
	@command -v infracost >/dev/null 2>&1 || { echo "$(RED)infracost not installed. Visit https://www.infracost.io/docs/$(NC)"; exit 1; }
	cd $(TERRAFORM_DIR) && infracost breakdown --path .
	@echo "$(GREEN)Cost estimate complete$(NC)"

package: build ## Create distributable package
	@echo "$(YELLOW)Creating distribution package...$(NC)"
	tar -czf $(PROJECT_NAME)-dist.tar.gz $(DIST_DIR) README.md
	@echo "$(GREEN)Package created: $(PROJECT_NAME)-dist.tar.gz$(NC)"

.DEFAULT_GOAL := help
