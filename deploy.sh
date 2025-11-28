#!/bin/bash
set -e

echo "Building and deploying PartingPal Frontend to Google Cloud..."

# Configuration
PROJECT_ID="trim-tributary-479605-e9"
SERVICE_NAME="market watch"
REGION="us-central1"
IMAGE_NAME="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
VERSION="v$(date +%Y%m%d-%H%M%S)"

echo "Project ID: $PROJECT_ID"
echo "Service: $SERVICE_NAME"
echo "Region: $REGION"
echo "Image: $IMAGE_NAME:$VERSION"

# Step 1: Authenticate with GCP
echo "Step 1: Authenticating with Google Cloud..."
gcloud auth login
gcloud config set project $PROJECT_ID

# Step 2: Enable required APIs
echo "Step 2: Enabling required GCP APIs..."
gcloud services enable cloudbuild.googleapis.com
gcloud services enable run.googleapis.com
gcloud services enable containerregistry.googleapis.com
gcloud services enable cloudsql.googleapis.com

# Step 3: Build Docker image
echo "Step 3: Building Docker image..."
gcloud builds submit --tag $IMAGE_NAME:$VERSION --tag $IMAGE_NAME:$VERSION .

# Step 4: Deploy to Cloud Run
echo "Step 4: Deploying to Cloud Run..."
gcloud run deploy $SERVICE_NAME \
  --image=$IMAGE_NAME:$VERSION \
  --platform=managed \
  --region=$REGION \
  --allow-unauthenticated \
  --memory=2Gi \
  --cpu=2 \
  --timeout=3600 \
  --max-instances=10 \
  --set-env-vars="GOOGLE_API_KEY=${AIzaSyAbb3uBa_vOTeZ14y2WDw6Nzq6eRBqE5fo}" \
  --vpc-connector=projects/$PROJECT_ID/locations/$REGION/connectors/default

echo "Step 5: Retrieving service URL..."
SERVICE_URL=$(gcloud run services describe $SERVICE_NAME --region=$REGION --format='value(status.url)')

echo "====================================="
echo "   Deployment Successful!"
echo "====================================="
echo "Service URL: $SERVICE_URL"
echo "Version: $VERSION"
echo "Region: $REGION"
echo ""
echo "To view logs:"
echo "gcloud run logs read $SERVICE_NAME --region=$REGION --limit=50"
echo ""
echo "To update traffic:"
echo "gcloud run services update-traffic $SERVICE_NAME --region=$REGION"