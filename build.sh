#!/bin/bash

# Build script for Genome Data Pipeline Lambda
# This script creates deployment packages for Lambda function and layer

set -e

PROJECT_ROOT=$(pwd)
BUILD_DIR="$PROJECT_ROOT/build"
DIST_DIR="$PROJECT_ROOT/dist"

echo "=== Building Genome Data Pipeline Lambda Package ==="

# Clean previous builds
rm -rf "$BUILD_DIR" "$DIST_DIR"
mkdir -p "$BUILD_DIR" "$DIST_DIR"

# ============================================
# Step 1: Build C++ Parser
# ============================================
echo ""
echo "Step 1: Building C++ FASTA/FASTQ Parser..."

# Compile C++ parser inside Amazon Linux 2 container (matches Lambda runtime)
echo "Compiling fasta_parser.cpp inside Amazon Linux 2 container..."

if ! command -v docker &> /dev/null; then
    echo "ERROR: Docker is required to compile the C++ parser for Lambda (Amazon Linux 2)"
    exit 1
fi

mkdir -p "$BUILD_DIR"

docker run --rm \
    -v "$PROJECT_ROOT:/src" \
    -v "$BUILD_DIR:/build" \
    amazonlinux:2 \
    bash -c "
        set -e
        yum install -y gcc-c++ wget 2>&1 | tail -5
        echo 'Downloading nlohmann/json...'
        wget -q https://github.com/nlohmann/json/releases/download/v3.11.3/json.hpp \
             -O /build/json.hpp
        echo 'Compiling...'
        g++ -O3 -std=c++17 \
            -I/build \
            -static-libgcc -static-libstdc++ \
            /src/fasta_parser.cpp \
            -o /build/fasta_parser
        echo 'Compilation successful'
    "

echo "C++ parser compiled successfully (Amazon Linux 2 binary)"

# ============================================
# Step 2: Build Lambda Layer
# ============================================
echo ""
echo "Step 2: Building Lambda Layer..."

LAYER_DIR="$BUILD_DIR/layer"
SITE_PACKAGES_DIR="$LAYER_DIR/python/lib/python3.11/site-packages"
mkdir -p "$SITE_PACKAGES_DIR"
mkdir -p "$LAYER_DIR/bin"

# Copy C++ binary to layer
cp "$BUILD_DIR/fasta_parser" "$LAYER_DIR/bin/"
chmod +x "$LAYER_DIR/bin/fasta_parser"

# Install Python dependencies to layer
echo "Installing Python dependencies..."
python3 -m pip install -r requirements.txt \
    --target "$SITE_PACKAGES_DIR" \
    --platform manylinux2014_x86_64 \
    --only-binary=:all: \
    --upgrade

find "$SITE_PACKAGES_DIR" -type d \( -name tests -o -name test -o -name __pycache__ -o -name include -o -name src \) -prune -exec rm -rf {} +
find "$SITE_PACKAGES_DIR" -type d -name "*.dist-info" -prune -exec rm -rf {} +
find "$SITE_PACKAGES_DIR" -type f \( -name "*.pyc" -o -name "*.pyo" -o -name "*.pxd" -o -name "*.pyi" \) -delete

# Create layer zip
cd "$LAYER_DIR"
zip -r "$DIST_DIR/lambda_layer.zip" . -q
cd "$PROJECT_ROOT"

echo "Lambda layer created: $DIST_DIR/lambda_layer.zip"
echo "Layer size: $(du -h $DIST_DIR/lambda_layer.zip | cut -f1)"

# ============================================
# Step 3: Build Lambda Function Package
# ============================================
echo ""
echo "Step 3: Building Lambda Function Package..."

FUNCTION_DIR="$BUILD_DIR/function"
mkdir -p "$FUNCTION_DIR"

# Copy Lambda handler
cp lambda_handler.py "$FUNCTION_DIR/"

# Create function zip
cd "$FUNCTION_DIR"
zip -r "$DIST_DIR/lambda_function.zip" . -q
cd "$PROJECT_ROOT"

echo "Lambda function created: $DIST_DIR/lambda_function.zip"
echo "Function size: $(du -h $DIST_DIR/lambda_function.zip | cut -f1)"

# ============================================
# Step 4: Build Dashboard API Function Package
# ============================================
echo ""
echo "Step 4: Building Dashboard API Function Package..."

WEB_API_DIR="$BUILD_DIR/web_api"
mkdir -p "$WEB_API_DIR"

cp web_api_handler.py "$WEB_API_DIR/"

cd "$WEB_API_DIR"
zip -r "$DIST_DIR/web_api_function.zip" . -q
cd "$PROJECT_ROOT"

echo "Dashboard API function created: $DIST_DIR/web_api_function.zip"
echo "Function size: $(du -h $DIST_DIR/web_api_function.zip | cut -f1)"

# ============================================
# Step 5: Copy Terraform files
# ============================================
echo ""
echo "Step 5: Preparing Terraform configuration..."

mkdir -p "$DIST_DIR/terraform"
cp main.tf "$DIST_DIR/terraform/"
cp "$DIST_DIR/lambda_layer.zip" "$DIST_DIR/terraform/"
cp "$DIST_DIR/lambda_function.zip" "$DIST_DIR/terraform/"
cp "$DIST_DIR/web_api_function.zip" "$DIST_DIR/terraform/"
rm -rf "$DIST_DIR/terraform/webapp"
cp -R webapp "$DIST_DIR/terraform/"

# ============================================
# Summary
# ============================================
echo ""
echo "=== Build Complete ==="
echo ""
echo "Artifacts created in: $DIST_DIR"
echo "  - lambda_layer.zip    : Lambda layer with C++ parser and dependencies"
echo "  - lambda_function.zip : Lambda function code"
echo "  - web_api_function.zip : Dashboard API Lambda function code"
echo "  - terraform/          : Infrastructure as Code"
echo ""
echo "Next steps:"
echo "  1. cd $DIST_DIR/terraform"
echo "  2. terraform init"
echo "  3. terraform plan"
echo "  4. terraform apply"
echo ""
echo "To test the C++ parser locally:"
echo "  $BUILD_DIR/fasta_parser <input.fasta> <output.json>"
