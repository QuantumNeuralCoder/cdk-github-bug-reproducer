# GitHub Issue Migration Utility

This utility script helps you migrate issues from one GitHub repository to another. It creates new issues in the destination repository based on issues from the source repository, and adds the "bug" label to each new issue.

## Features

- Migrate specific issues by number
- Add "bug" label to all migrated issues
- Preserve original issue content and attribution
- Option to include comments from original issues
- Option to add reference links to original issues
- Dry run mode to preview migrations without creating issues

## Setup

### Create a Python 3.12 Virtual Environment

1. Make sure Python 3.12 is installed on your system
2. Create a virtual environment:

```bash
# Navigate to the utilities directory
cd /path/to/cdk-github-bug-reproducer/utilities

# Create a virtual environment
python3.12 -m venv .venv

# Activate the virtual environment
# On macOS/Linux:
source .venv/bin/activate
# On Windows:
# .venv\Scripts\activate

# Install required packages
pip install -r requirements.txt
```

## Usage

Basic usage:

```bash
python issue_migrator.py --token YOUR_GITHUB_TOKEN --source owner/repo --dest owner/repo --issues 1,2,3
```

### Command Line Arguments

| Argument | Description |
|----------|-------------|
| `--token` | GitHub personal access token (required) |
| `--source` | Source repository in format owner/repo (required) |
| `--dest` | Destination repository in format owner/repo (required) |
| `--issues` | Comma-separated list of issue numbers to migrate (required) |
| `--dry-run` | Perform a dry run without creating issues |
| `--add-reference` | Add reference to original issue in the new issue |
| `--include-comments` | Include comments from original issues |

### Examples

Migrate issues with comments and references:

```bash
python issue_migrator.py --token ghp_YOUR_TOKEN --source owner/repo1 --dest owner/repo2 --issues 1,5,10 --include-comments --add-reference
```

Perform a dry run to preview what would happen:

```bash
python issue_migrator.py --token ghp_YOUR_TOKEN --source owner/repo1 --dest owner/repo2 --issues 1,5,10 --dry-run
```

## GitHub Token Permissions

Your GitHub token needs the following permissions:
- `repo` scope for private repositories
- `public_repo` scope for public repositories

## Notes

- The script adds a 1-second delay between issue creations to avoid hitting GitHub API rate limits
- All migrated issues will have the "bug" label added
- Original issue labels are preserved
- The script attributes the original author in the issue body