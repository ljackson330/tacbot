#!/bin/bash
echo "Formatting code..."
black .

echo "Checking code style..."
flake8 .

echo "Running tests..."
pytest

echo "All checks complete."