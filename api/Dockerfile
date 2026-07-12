# Cloud Run image for the Loupe typed FastAPI boundary.
#
# This Dockerfile imports shared/ and apps/ Python packages, not just
# api/, so the build context MUST be the repository root:
#
#   gcloud builds submit --tag <image> -f api/Dockerfile .
#
# or, with Cloud Run's source-based deploy (also root context):
#
#   gcloud run deploy loupe-api --source . --dockerfile api/Dockerfile ...

FROM python:3.11-slim

WORKDIR /app

# Install the project (api/, apps/, shared/, tools/) as declared in
# pyproject.toml. Runtime image only -- no [dev] extras.
COPY pyproject.toml ./
COPY api ./api
COPY apps ./apps
COPY shared ./shared
COPY tools ./tools

RUN pip install --no-cache-dir .

# Cloud Run injects PORT at runtime; uvicorn must bind to it.
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT}"]
