from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import textwrap
from tempfile import TemporaryDirectory

import pytest


ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github/workflows/import-staging.yml"
PRODUCTION_WORKFLOW = ROOT / ".github/workflows/import-production.yml"


def test_staging_import_is_manual_fixed_and_sequential() -> None:
    workflow = WORKFLOW.read_text()

    assert "workflow_dispatch:" in workflow
    assert "schedule:" not in workflow
    assert "environment: staging" in workflow
    assert "production" not in workflow
    assert "contents: read" in workflow
    assert "id-token: write" in workflow
    assert "timeout-minutes: 90" in workflow
    assert "cancel-in-progress: false" in workflow
    assert 'seasons=(2020 2021 2022 2023 2024 2025 2026)' in workflow
    assert workflow.count("python -m backend.nospoil_nfl.sync.import_schedule") == 1
    assert workflow.count("python -m backend.nospoil_nfl.rating.reconcile") == 1
    assert "--mode correction --season" in workflow


def test_production_import_is_manual_fixed_and_sequential() -> None:
    workflow = PRODUCTION_WORKFLOW.read_text()

    assert "workflow_dispatch:" in workflow
    assert "schedule:" not in workflow
    assert "environment: production" in workflow
    assert "nospoil-production-games" in workflow
    assert "nospoil-staging-games" not in workflow
    assert '[[ "$NOSPOIL_SCHEDULE_INDEX" == "season-schedule-index" ]]' in workflow
    assert '[[ "$NOSPOIL_IMPORT_ROLE_ARN" == "arn:aws:iam::945461162255:role/nospoil-production-import" ]]' in workflow
    assert "contents: read" in workflow
    assert "id-token: write" in workflow
    assert 'seasons=(2020 2021 2022 2023 2024 2025 2026)' in workflow
    assert workflow.count("python -m backend.nospoil_nfl.sync.import_schedule") == 1
    assert workflow.count("python -m backend.nospoil_nfl.rating.reconcile") == 1
    assert "Production inventory completed with attention required." in workflow


@pytest.mark.parametrize("failure_stage", ["import", "reconcile", "malformed"])
def test_season_import_and_reconciliation_failures_do_not_stop_later_seasons(
    failure_stage: str,
) -> None:
    """Execute the workflow shell with fake providers to test its control flow."""
    workflow = WORKFLOW.read_text()
    start = workflow.index("        run: |", workflow.index("Import every reviewed season"))
    lines = workflow[start:].splitlines()[1:]
    run_lines = []
    for line in lines:
        if line and not line.startswith("          "):
            break
        run_lines.append(line[10:] if line else "")
    script = textwrap.dedent("\n".join(run_lines))

    with TemporaryDirectory() as temp:
        fake_python = Path(temp) / "python"
        fake_python.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
  if [[ "$*" == *"sync.import_schedule"* ]]; then
    season="${!#}"
    printf 'import:%s\\n' "$season" >> "$EVENT_LOG"
    if [[ "$season" == 2022 && "$FAILURE_STAGE" == malformed ]]; then
      printf 'not-supported-final-ids\\n' > "$GITHUB_OUTPUT"
    else
      printf 'supported_final_game_ids=["game-%s"]\\n' "$season" > "$GITHUB_OUTPUT"
    fi
  if [[ "$season" == 2021 && "$FAILURE_STAGE" == import ]]; then exit 7; fi
else
  season=""
  for ((i=1; i<=$#; i++)); do
    [[ "${!i}" == "--season" ]] && { j=$((i + 1)); season="${!j}"; }
  done
  printf 'reconcile:%s\\n' "$season" >> "$EVENT_LOG"
  if [[ "$season" == 2021 && "$FAILURE_STAGE" == reconcile ]]; then exit 9; fi
fi
"""
        )
        fake_python.chmod(0o755)
        event_log = Path(temp) / "events"
        env = os.environ | {
            "PATH": f"{temp}:{os.environ['PATH']}",
            "EVENT_LOG": str(event_log),
            "FAILURE_STAGE": failure_stage,
        }
        result = subprocess.run(
            ["bash", "-c", script], env=env, text=True, capture_output=True
        )
        assert result.returncode != 0
        events = event_log.read_text().splitlines()
        imports = [event for event in events if event.startswith("import:")]
        reconciles = [event for event in events if event.startswith("reconcile:")]
        assert imports == [f"import:{season}" for season in range(2020, 2027)]
        expected_reconcile_seasons = range(2020, 2027)
        if failure_stage == "import":
            expected_reconcile_seasons = (2020, 2022, 2023, 2024, 2025, 2026)
        elif failure_stage == "malformed":
            expected_reconcile_seasons = (2020, 2021, 2023, 2024, 2025, 2026)
        assert reconciles == [f"reconcile:{season}" for season in expected_reconcile_seasons]
        assert events == imports + reconciles
        assert "attention required" in result.stdout


def test_workflow_uses_pinned_oidc_bounded_dependencies_and_validated_ids() -> None:
    workflow = WORKFLOW.read_text()

    assert "actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in workflow
    assert "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065" in workflow
    assert (
        "aws-actions/configure-aws-credentials@cbe3b392738ccf3f987d68400dafcf4b0624a56c"
    ) in workflow
    assert "pip install -r backend/requirements-reconcile.txt" in workflow
    assert (
        "NOSPOIL_ESPN_TIMEOUT_SECONDS: ${{ vars.NOSPOIL_ESPN_TIMEOUT_SECONDS }}"
        in workflow
    )
    assert (
        "NOSPOIL_NFLVERSE_TIMEOUT_SECONDS: ${{ vars.NOSPOIL_NFLVERSE_TIMEOUT_SECONDS }}"
    ) in workflow
    assert '"nospoil-staging-games"' in workflow
    assert "role-session-name: nospoil-staging-import" in workflow
    assert 'GITHUB_OUTPUT="$output"' in workflow
    assert 'jq -e \'type == "array"' in workflow
    assert 'test("^[A-Za-z0-9._:-]+$")' in workflow
    assert 'reconcile_args+=(--game-id "$game_id")' in workflow
    assert "trap 'rm -rf \"$workdir\"' EXIT" in workflow
    assert "NOSPOIL_IMPORT_ROLE_ARN: ${{ vars.NOSPOIL_IMPORT_ROLE_ARN }}" in workflow
    assert "role-to-assume: ${{ vars.NOSPOIL_IMPORT_ROLE_ARN }}" in workflow
    assert '"$NOSPOIL_IMPORT_ROLE_ARN"' in workflow
    assert "NOSPOIL_OPERATIONS_ROLE_ARN" not in workflow


def test_import_permission_is_table_only_and_query_remains_index_only() -> None:
    template = json.loads((ROOT / "infra/environment.template.json").read_text())
    role = template["Resources"]["GitHubImportRole"]
    assert "Condition" not in role
    assert role["Properties"]["RoleName"] == {
        "Fn::Sub": "nospoil-${Environment}-import"
    }
    statements = role["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]

    assert statements == [
        {
            "Effect": "Allow",
            "Action": [
                "dynamodb:GetItem",
                "dynamodb:PutItem",
                "dynamodb:UpdateItem",
            ],
            "Resource": {"Fn::GetAtt": ["GamesTable", "Arn"]},
        },
        {
            "Effect": "Allow",
            "Action": "dynamodb:Query",
            "Resource": {"Fn::Sub": "${GamesTable.Arn}/index/season-schedule-index"},
        },
    ]
    all_actions = {
        action
        for statement in statements
        for action in (
            statement["Action"]
            if isinstance(statement["Action"], list)
            else [statement["Action"]]
        )
    }
    assert "dynamodb:Scan" not in all_actions
    assert "dynamodb:DeleteItem" not in all_actions
    assert "lambda:InvokeFunction" not in all_actions
    assert (
        role["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]["Condition"][
            "StringLike"
        ]["token.actions.githubusercontent.com:sub"]["Fn::Sub"]
        == "repo:${GitHubRepository}:environment:${Environment}"
    )
    trust = role["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]
    assert trust["Condition"]["StringEquals"] == {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
    }
    assert template["Outputs"]["ImportRoleArn"] == {
        "Description": "Environment-bound GitHub OIDC role for inventory import and targeted correction.",
        "Value": {"Fn::GetAtt": ["GitHubImportRole", "Arn"]},
    }
