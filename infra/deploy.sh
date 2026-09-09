#!/usr/bin/env bash
set -euo pipefail

environment="${1:-}"
if [[ "$environment" != "staging" && "$environment" != "production" ]]; then
  echo "Usage: $0 staging|production" >&2
  exit 2
fi

AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-}}"
NOSPOIL_FRONTEND_ORIGIN="${NOSPOIL_FRONTEND_ORIGIN:-}"
NOSPOIL_ALERT_EMAIL="${NOSPOIL_ALERT_EMAIL:-}"
GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-paul-atwal/nospoilers}"
NOSPOIL_ACTIVE_SEASON="${NOSPOIL_ACTIVE_SEASON:-2026}"
NOSPOIL_SCHEDULE_STATE="${NOSPOIL_SCHEDULE_STATE:-DISABLED}"
NOSPOIL_MONTHLY_BUDGET_USD="${NOSPOIL_MONTHLY_BUDGET_USD:-5}"

[[ -n "$AWS_REGION" ]] || { echo "AWS_REGION is required" >&2; exit 2; }
[[ -n "$NOSPOIL_FRONTEND_ORIGIN" ]] || {
  echo "NOSPOIL_FRONTEND_ORIGIN is required" >&2
  exit 2
}
[[ -n "$NOSPOIL_ALERT_EMAIL" ]] || {
  echo "NOSPOIL_ALERT_EMAIL is required" >&2
  exit 2
}
[[ "$NOSPOIL_SCHEDULE_STATE" == "DISABLED" || "$NOSPOIL_SCHEDULE_STATE" == "ENABLED" ]] || {
  echo "NOSPOIL_SCHEDULE_STATE must be DISABLED or ENABLED" >&2
  exit 2
}

aws sts get-caller-identity >/dev/null || {
  echo "AWS credentials are required" >&2
  exit 2
}

remove_failed_stack() {
  local stack_name="$1"
  local stack_status
  stack_status="$(aws cloudformation describe-stacks \
    --region "$AWS_REGION" \
    --stack-name "$stack_name" \
    --query 'Stacks[0].StackStatus' \
    --output text 2>/dev/null || true)"
  case "$stack_status" in
    ROLLBACK_COMPLETE|ROLLBACK_FAILED|DELETE_FAILED)
      echo "Removing failed stack before retry: $stack_name" >&2
      aws cloudformation delete-stack \
        --region "$AWS_REGION" \
        --stack-name "$stack_name"
      if ! aws cloudformation wait stack-delete-complete \
        --region "$AWS_REGION" \
        --stack-name "$stack_name"; then
        aws cloudformation describe-stack-events \
          --region "$AWS_REGION" \
          --stack-name "$stack_name" \
          --output table >&2 || true
        exit 1
      fi
      ;;
  esac
}

foundation_stack="nospoil-foundation"
environment_stack="nospoil-${environment}"
artifact_dir="$(mktemp -d)"
trap 'rm -rf "$artifact_dir"' EXIT

python3 infra/build_packages.py --output-dir "$artifact_dir" >/dev/null
read_zip="$(find "$artifact_dir" -maxdepth 1 -name 'read-*.zip' -print -quit)"
sync_zip="$(find "$artifact_dir" -maxdepth 1 -name 'sync-*.zip' -print -quit)"
if [[ ! -f "$read_zip" || ! -f "$sync_zip" ]]; then
  echo "Lambda package build did not produce both archives" >&2
  exit 1
fi

remove_failed_stack "$foundation_stack"
if ! aws cloudformation deploy \
  --region "$AWS_REGION" \
  --stack-name "$foundation_stack" \
  --template-file infra/foundation.template.json \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --parameter-overrides \
    AlertEmail="$NOSPOIL_ALERT_EMAIL" \
    MonthlyBudgetUSD="$NOSPOIL_MONTHLY_BUDGET_USD"; then
  aws cloudformation describe-stack-events \
    --region "$AWS_REGION" \
    --stack-name "$foundation_stack" \
    --output table >&2 || true
  exit 1
fi

bucket="$(aws cloudformation describe-stacks \
  --region "$AWS_REGION" \
  --stack-name "$foundation_stack" \
  --query 'Stacks[0].Outputs[?OutputKey==`ArtifactBucketName`].OutputValue' \
  --output text)"
topic="$(aws cloudformation describe-stacks \
  --region "$AWS_REGION" \
  --stack-name "$foundation_stack" \
  --query 'Stacks[0].Outputs[?OutputKey==`OperationsTopicArn`].OutputValue' \
  --output text)"
[[ -n "$bucket" && "$bucket" != "None" ]] || {
  echo "Foundation did not return an artifact bucket" >&2
  exit 1
}
[[ -n "$topic" && "$topic" != "None" ]] || {
  echo "Foundation did not return an operations topic" >&2
  exit 1
}

read_key="${environment}/$(basename "$read_zip")"
sync_key="${environment}/$(basename "$sync_zip")"
code_sha256="$(openssl dgst -sha256 -binary "$sync_zip" | openssl base64 | tr -d '\n')"

aws s3 cp --region "$AWS_REGION" "$read_zip" "s3://${bucket}/${read_key}"
aws s3 cp --region "$AWS_REGION" "$sync_zip" "s3://${bucket}/${sync_key}"

remove_failed_stack "$environment_stack"
if ! aws cloudformation deploy \
  --region "$AWS_REGION" \
  --stack-name "$environment_stack" \
  --template-file infra/environment.template.json \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --parameter-overrides \
    Environment="$environment" \
    ArtifactBucket="$bucket" \
    ReadArtifactKey="$read_key" \
    SyncArtifactKey="$sync_key" \
    FrontendOrigin="$NOSPOIL_FRONTEND_ORIGIN" \
    ActiveSeason="$NOSPOIL_ACTIVE_SEASON" \
    ScheduleState="$NOSPOIL_SCHEDULE_STATE" \
    CodeSha256="$code_sha256" \
    OperationsTopicArn="$topic" \
    GitHubRepository="$GITHUB_REPOSITORY"; then
  aws cloudformation describe-stack-events \
    --region "$AWS_REGION" \
    --stack-name "$environment_stack" \
    --output table >&2 || true
  exit 1
fi

aws cloudformation describe-stacks \
  --region "$AWS_REGION" \
  --stack-name "$environment_stack" \
  --query 'Stacks[0].Outputs' \
  --output table
