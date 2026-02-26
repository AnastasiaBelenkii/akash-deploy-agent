# Akash Deploy Agent

A FastAPI web service that uses Llama 3.3 70B on AkashML to process natural language deployment commands for Akash Network.

## Quick Start

### Using Docker

```bash
docker run -d \
  -p 8080:8080 \
  -e AKASH_API_KEY=your_key_here \
  -e AKASHML_API_KEY=your_key_here \
  hoskayne/akash-deploy-agent:latest
```

### Using Docker Compose

1. Copy `.env.example` to `.env` and fill in your API keys
2. Run: `docker-compose up -d`

### Deploying to Akash Network

```bash
# Deploy this image to Akash
python deploy.py deploy hoskayne/akash-deploy-agent:latest --port 8080
```

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `AKASH_API_KEY` | Akash Console API key | Yes |
| `AKASHML_API_KEY` | AkashML API key | Yes |

## Usage

Once running, open http://localhost:8080 to use the web interface.

Example commands:
- "Deploy nginx on port 80"
- "List all deployments"
- "Close deployment 12345"

## Repository

- GitHub: https://github.com/AnastasiaBelenkii/akash-deploy-agent
- Docker Hub: https://hub.docker.com/r/hoskayne/akash-deploy-agent
