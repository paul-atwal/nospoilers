from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github/workflows/import-staging.yml"


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
    assert "for season in 2020 2021 2022 2023 2024 2025 2026" in workflow
    assert workflow.count("python -m backend.nospoil_nfl.sync.import_schedule") == 1
    assert workflow.count("python -m backend.nospoil_nfl.rating.reconcile") == 1
    assert "--mode correction --season" in workflow


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
    assert "trap 'rm -f \"$output\"' EXIT" in workflow
    assert "NOSPOIL_IMPORT_ROLE_ARN: ${{ vars.NOSPOIL_IMPORT_ROLE_ARN }}" in workflow
    assert "role-to-assume: ${{ vars.NOSPOIL_IMPORT_ROLE_ARN }}" in workflow
    assert '"$NOSPOIL_IMPORT_ROLE_ARN"' in workflow
    assert "NOSPOIL_OPERATIONS_ROLE_ARN" not in workflow


def test_import_permission_is_table_only_and_query_remains_index_only() -> None:
    template = json.loads((ROOT / "infra/environment.template.json").read_text())
    role = template["Resources"]["GitHubImportRole"]
    assert role["Condition"] == "IsStaging"
    assert template["Conditions"]["IsStaging"] == {
        "Fn::Equals": [{"Ref": "Environment"}, "staging"]
    }
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
        == "repo:${GitHubRepository}:environment:staging"
    )
    trust = role["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]
    assert trust["Condition"]["StringEquals"] == {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
    }
    assert template["Outputs"]["ImportRoleArn"] == {
        "Condition": "IsStaging",
        "Description": "Staging-only GitHub OIDC role for inventory import and targeted correction.",
        "Value": {"Fn::GetAtt": ["GitHubImportRole", "Arn"]},
    }
