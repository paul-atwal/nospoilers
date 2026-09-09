from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_manual_repair_workflow_is_oidc_bound_and_orders_repair_before_rating() -> None:
    workflow = (ROOT / ".github/workflows/repair-schedule.yml").read_text()
    assert "schedule:" not in workflow
    assert "contents: read" in workflow
    assert "id-token: write" in workflow
    assert "environment: ${{ inputs.environment }}" in workflow
    assert "NOSPOIL_OPERATIONS_ROLE_ARN" in workflow
    assert (
        "NOSPOIL_NFLVERSE_TIMEOUT_SECONDS: ${{ vars.NOSPOIL_NFLVERSE_TIMEOUT_SECONDS }}"
    ) in workflow
    assert "pip install -r backend/requirements-reconcile.txt" in workflow
    repair = workflow.index("python -m backend.nospoil_nfl.sync.repair")
    reconcile = workflow.index("python -m backend.nospoil_nfl.rating.reconcile")
    assert repair < reconcile
    assert "steps.repair.outputs.season" in workflow
    assert "steps.repair.outputs.reconciliation_game_ids" in workflow
    assert 'args=(--mode correction --season "$REPAIR_SEASON")' in workflow
    assert 'args+=(--game-id "$game_id")' in workflow
    assert "role-session-name: nospoil-schedule-repair" in workflow
    assert '--season "$SEASON" --phase "$PHASE" --week "$WEEK"' in workflow


def test_environment_exports_operations_role_with_repair_permissions() -> None:
    template = json.loads((ROOT / "infra/environment.template.json").read_text())
    role = template["Resources"]["GitHubReconcileRole"]
    statements = role["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
    actions = {
        action
        for statement in statements
        for action in (
            statement["Action"]
            if isinstance(statement["Action"], list)
            else [statement["Action"]]
        )
    }
    assert actions == {
        "dynamodb:GetItem",
        "dynamodb:Query",
        "dynamodb:UpdateItem",
    }
    assert statements == [
        {
            "Effect": "Allow",
            "Action": [
                "dynamodb:GetItem",
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
    trust = role["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]
    assert trust["Action"] == "sts:AssumeRoleWithWebIdentity"
    assert (
        "environment:${Environment}"
        in trust["Condition"]["StringLike"]["token.actions.githubusercontent.com:sub"][
            "Fn::Sub"
        ]
    )
    assert template["Outputs"]["OperationsRoleArn"]["Value"] == {
        "Fn::GetAtt": ["GitHubReconcileRole", "Arn"]
    }
