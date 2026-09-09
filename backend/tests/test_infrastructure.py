"""Stdlib contract checks for the D1 deployment foundation."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).parents[2]


def _inline_actions(role: dict) -> set[str]:
    statements = role["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
    return {
        action
        for statement in statements
        for action in (
            statement["Action"]
            if isinstance(statement["Action"], list)
            else [statement["Action"]]
        )
    }


class InfrastructureContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.env = json.loads((ROOT / "infra/environment.template.json").read_text())
        self.foundation = json.loads(
            (ROOT / "infra/foundation.template.json").read_text()
        )
        self.resources = self.env["Resources"]

    def test_table_is_isolated_on_demand_encrypted_and_retained(self) -> None:
        table = self.resources["GamesTable"]
        properties = table["Properties"]
        self.assertEqual(
            properties["TableName"]["Fn::Sub"], "nospoil-${Environment}-games"
        )
        self.assertEqual(properties["BillingMode"], "PAY_PER_REQUEST")
        self.assertEqual(properties["SSESpecification"], {"SSEEnabled": True})
        self.assertEqual(table["DeletionPolicy"], "RetainExceptOnCreate")
        self.assertEqual(table["UpdateReplacePolicy"], "Retain")
        self.assertEqual(
            properties["GlobalSecondaryIndexes"][0]["IndexName"],
            "season-schedule-index",
        )
        self.assertNotIn("ProvisionedThroughput", properties)

    def test_functions_are_bounded_and_do_not_use_a_vpc(self) -> None:
        read = self.resources["ReadFunction"]["Properties"]
        sync = self.resources["SyncFunction"]["Properties"]
        self.assertEqual(
            read["Handler"], "backend.nospoil_nfl.api.handler.lambda_handler"
        )
        self.assertEqual(
            sync["Handler"], "backend.nospoil_nfl.sync.handler.lambda_handler"
        )
        self.assertEqual((read["Timeout"], read["MemorySize"]), (10, 256))
        self.assertEqual((sync["Timeout"], sync["MemorySize"]), (45, 512))
        self.assertEqual(read["ReservedConcurrentExecutions"], 4)
        self.assertEqual(sync["ReservedConcurrentExecutions"], 1)
        self.assertNotIn("VpcConfig", read)
        self.assertNotIn("VpcConfig", sync)
        self.assertEqual(
            read["Environment"]["Variables"]["NOSPOIL_SCHEDULE_INDEX"],
            "season-schedule-index",
        )
        self.assertEqual(
            sync["Environment"]["Variables"]["NOSPOIL_ESPN_TIMEOUT_SECONDS"], "8"
        )
        self.assertEqual(
            sync["Environment"]["Variables"]["NOSPOIL_ESPN_SUMMARY_TIMEOUT_SECONDS"],
            "5",
        )

    def test_runtime_roles_are_least_privilege(self) -> None:
        self.assertEqual(
            _inline_actions(self.resources["ReadRole"]),
            {"dynamodb:Query", "logs:CreateLogStream", "logs:PutLogEvents"},
        )
        self.assertEqual(
            _inline_actions(self.resources["SyncRole"]),
            {
                "dynamodb:GetItem",
                "dynamodb:PutItem",
                "dynamodb:UpdateItem",
                "dynamodb:Query",
                "logs:CreateLogStream",
                "logs:PutLogEvents",
            },
        )
        self.assertEqual(
            _inline_actions(self.resources["GitHubReconcileRole"]),
            {
                "dynamodb:GetItem",
                "dynamodb:Query",
                "dynamodb:UpdateItem",
            },
        )
        trust = self.resources["GitHubReconcileRole"]["Properties"][
            "AssumeRolePolicyDocument"
        ]["Statement"][0]
        self.assertEqual(trust["Action"], "sts:AssumeRoleWithWebIdentity")
        self.assertEqual(
            trust["Condition"]["StringLike"]["token.actions.githubusercontent.com:sub"][
                "Fn::Sub"
            ],
            "repo:${GitHubRepository}:environment:${Environment}",
        )
        self.assertEqual(
            _inline_actions(self.resources["GitHubImportRole"]),
            {
                "dynamodb:GetItem",
                "dynamodb:PutItem",
                "dynamodb:UpdateItem",
                "dynamodb:Query",
            },
        )
        self.assertEqual(self.resources["GitHubImportRole"]["Condition"], "IsStaging")
        self.assertEqual(
            self.env["Conditions"]["IsStaging"],
            {"Fn::Equals": [{"Ref": "Environment"}, "staging"]},
        )
        self.assertEqual(self.env["Outputs"]["ImportRoleArn"]["Condition"], "IsStaging")
        self.assertEqual(
            self.env["Outputs"]["ImportRoleArn"]["Value"],
            {"Fn::GetAtt": ["GitHubImportRole", "Arn"]},
        )

    def test_public_read_url_has_both_required_permissions(self) -> None:
        self.assertEqual(self.resources["ReadUrlPermission"]["DependsOn"], ["ReadUrl"])
        self.assertEqual(
            self.resources["ReadUrlInvokePermission"]["DependsOn"], ["ReadUrl"]
        )
        first = self.resources["ReadUrlPermission"]["Properties"]
        second = self.resources["ReadUrlInvokePermission"]["Properties"]
        self.assertEqual(first["Action"], "lambda:InvokeFunctionUrl")
        self.assertEqual(first["FunctionUrlAuthType"], "NONE")
        self.assertEqual(second["Action"], "lambda:InvokeFunction")
        self.assertIs(second["InvokedViaFunctionUrl"], True)
        self.assertEqual(first["Principal"], "*")
        self.assertEqual(second["Principal"], "*")

    def test_scheduler_trust_is_limited_to_this_account(self) -> None:
        statement = self.resources["SchedulerRole"]["Properties"][
            "AssumeRolePolicyDocument"
        ]["Statement"][0]
        self.assertEqual(statement["Principal"], {"Service": "scheduler.amazonaws.com"})
        self.assertEqual(
            statement["Condition"]["StringEquals"]["aws:SourceAccount"],
            {"Ref": "AWS::AccountId"},
        )

    def test_sync_version_and_async_configs_are_bound_to_aliases(self) -> None:
        self.assertNotIn("CodeSha256", self.resources["SyncFunction"]["Properties"])
        version = self.resources["SyncVersion"]
        self.assertEqual(version["Properties"]["CodeSha256"], {"Ref": "CodeSha256"})
        self.assertEqual(version["UpdateReplacePolicy"], "Retain")
        for name, depends_on, alias, age, attempts in (
            ("LiveInvokeConfig", "LiveAlias", "live", 60, 0),
            ("ScheduleInvokeConfig", "ScheduleAlias", "schedule", 900, 2),
        ):
            resource = self.resources[name]
            self.assertEqual(resource["DependsOn"], [depends_on])
            self.assertEqual(resource["Properties"]["Qualifier"], alias)
            self.assertEqual(resource["Properties"]["MaximumEventAgeInSeconds"], age)
            self.assertEqual(resource["Properties"]["MaximumRetryAttempts"], attempts)
            self.assertNotIn("DependsOn", resource["Properties"])
            self.assertNotIn("FunctionVersion", resource["Properties"])

    def test_three_schedules_have_exact_cadence_targets_and_retries(self) -> None:
        schedules = {
            name: value
            for name, value in self.resources.items()
            if value["Type"] == "AWS::Scheduler::Schedule"
        }
        self.assertEqual(
            set(schedules), {"LiveSchedule", "DailySchedule", "WeeklySchedule"}
        )
        expected = {
            "LiveSchedule": ("rate(1 minute)", "LiveAlias", 60, 0, "live_tick"),
            "DailySchedule": (
                "cron(15 10 * * ? *)",
                "ScheduleAlias",
                900,
                2,
                "daily_near_term",
            ),
            "WeeklySchedule": (
                "cron(45 10 ? * TUE *)",
                "ScheduleAlias",
                900,
                2,
                "weekly_remaining",
            ),
        }
        for name, (expression, alias, age, attempts, mode) in expected.items():
            properties = schedules[name]["Properties"]
            target = properties["Target"]
            self.assertEqual(properties["FlexibleTimeWindow"], {"Mode": "OFF"})
            self.assertEqual(properties["ScheduleExpression"], expression)
            self.assertEqual(properties["State"], {"Ref": "ScheduleState"})
            self.assertEqual(target["Arn"], {"Ref": alias})
            self.assertEqual(
                target["RetryPolicy"],
                {"MaximumEventAgeInSeconds": age, "MaximumRetryAttempts": attempts},
            )
            raw_input = target["Input"]
            event_input = (
                raw_input.get("Fn::Sub", raw_input)
                if isinstance(raw_input, dict)
                else raw_input
            )
            self.assertIn(f'"mode":"{mode}"', event_input)
            self.assertIn("<aws.scheduler.scheduled-time>", event_input)
        live_input = schedules["LiveSchedule"]["Properties"]["Target"]["Input"][
            "Fn::Sub"
        ]
        self.assertIn('"season":${ActiveSeason}', live_input)

    def test_logs_and_alarms_are_finite_and_actionable(self) -> None:
        self.assertEqual(
            self.resources["ReadLogGroup"]["Properties"]["RetentionInDays"], 14
        )
        self.assertEqual(
            self.resources["SyncLogGroup"]["Properties"]["RetentionInDays"], 14
        )
        alarms = [
            item
            for item in self.resources.values()
            if item["Type"] == "AWS::CloudWatch::Alarm"
        ]
        self.assertEqual(len(alarms), 4)
        for alarm in alarms:
            properties = alarm["Properties"]
            self.assertEqual(
                properties["AlarmActions"], [{"Ref": "OperationsTopicArn"}]
            )
            self.assertEqual(properties["TreatMissingData"], "notBreaching")
            self.assertIn(properties["MetricName"], {"Errors", "Throttles"})

    def test_foundation_has_private_expiring_artifacts_oidc_and_budget(self) -> None:
        resources = self.foundation["Resources"]
        bucket = resources["ArtifactBucket"]
        self.assertEqual(bucket["DeletionPolicy"], "Retain")
        self.assertEqual(
            bucket["Properties"]["VersioningConfiguration"], {"Status": "Enabled"}
        )
        self.assertTrue(
            all(bucket["Properties"]["PublicAccessBlockConfiguration"].values())
        )
        self.assertEqual(
            bucket["Properties"]["LifecycleConfiguration"]["Rules"][0][
                "ExpirationInDays"
            ],
            30,
        )
        oidc = resources["GitHubOidcProvider"]["Properties"]
        self.assertEqual(oidc["Url"], "https://token.actions.githubusercontent.com")
        self.assertEqual(oidc["ClientIdList"], ["sts.amazonaws.com"])
        self.assertEqual(
            resources["OperationsSubscription"]["Properties"]["Protocol"], "email"
        )
        self.assertEqual(
            self.foundation["Parameters"]["MonthlyBudgetUSD"]["Default"], 5
        )
        self.assertEqual(
            resources["MonthlyBudget"]["Properties"]["Budget"]["BudgetLimit"],
            {"Amount": {"Ref": "MonthlyBudgetUSD"}, "Unit": "USD"},
        )
        notifications = resources["MonthlyBudget"]["Properties"][
            "NotificationsWithSubscribers"
        ]
        self.assertEqual(
            [
                (x["Notification"]["NotificationType"], x["Notification"]["Threshold"])
                for x in notifications
            ],
            [("FORECASTED", 80), ("ACTUAL", 100)],
        )

    def test_render_is_static_only_and_publication_is_manual(self) -> None:
        render = (ROOT / "render.yaml").read_text()
        self.assertEqual(render.count("  - type:"), 1)
        self.assertIn("name: nospoilers-web", render)
        self.assertIn("runtime: static", render)
        self.assertIn("buildCommand: npm ci && npm run build", render)
        self.assertIn("VITE_API_URL", render)
        self.assertIn("autoDeploy: false", render)
        self.assertNotIn("nospoil-api", render)
        self.assertNotIn("/api/*", render)

    def test_reconciliation_uses_environment_bound_short_lived_credentials(
        self,
    ) -> None:
        workflow = (ROOT / ".github/workflows/reconcile-ratings.yml").read_text()
        self.assertIn("id-token: write", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("environment: ${{ inputs.environment || 'staging' }}", workflow)
        self.assertIn(
            "role-to-assume: ${{ vars.NOSPOIL_RECONCILE_ROLE_ARN }}", workflow
        )
        self.assertNotIn("AWS_ACCESS_KEY_ID", workflow)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", workflow)

    def test_deploy_script_preserves_rollback_and_starts_schedules_disabled(
        self,
    ) -> None:
        script = (ROOT / "infra/deploy.sh").read_text()
        self.assertIn(
            'NOSPOIL_SCHEDULE_STATE="${NOSPOIL_SCHEDULE_STATE:-DISABLED}"', script
        )
        self.assertIn("--no-fail-on-empty-changeset", script)
        self.assertNotIn("--disable-rollback", script)
        self.assertNotIn("--on-failure DO_NOTHING", script)
        self.assertIn("ROLLBACK_COMPLETE|ROLLBACK_FAILED|DELETE_FAILED", script)
        self.assertIn('remove_failed_stack "$foundation_stack"', script)
        self.assertIn('remove_failed_stack "$environment_stack"', script)
        self.assertIn("cloudformation delete-stack", script)
        self.assertIn("cloudformation wait stack-delete-complete", script)
        self.assertIn('GitHubRepository="$GITHUB_REPOSITORY"', script)
        self.assertIn('CodeSha256="$code_sha256"', script)


class PackageContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location(
            "nospoil_build_packages", ROOT / "infra/build_packages.py"
        )
        assert spec and spec.loader
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_zip_writer_is_deterministic_and_preserves_package_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            (source / "backend" / "nospoil_nfl").mkdir(parents=True)
            (source / "backend" / "nospoil_nfl" / "handler.py").write_text(
                "ok = True\n"
            )
            first = root / "first.zip"
            second = root / "second.zip"
            self.module._write_reproducible_zip(source, first)
            self.module._write_reproducible_zip(source, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                info = archive.getinfo("backend/nospoil_nfl/handler.py")
                self.assertEqual(info.date_time, self.module.ZIP_TIMESTAMP)

    def test_reconciliation_only_packages_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "pandas").mkdir()
            with self.assertRaisesRegex(RuntimeError, "reconciliation-only"):
                self.module._reject_excluded_dependencies(root)

    def test_application_copy_excludes_generated_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source" / "backend" / "nospoil_nfl"
            source.mkdir(parents=True)
            (source / "handler.py").write_text("ok = True\n")
            (source / ".DS_Store").write_bytes(b"metadata")
            cache = source / "__pycache__"
            cache.mkdir()
            (cache / "handler.cpython-313.pyc").write_bytes(b"bytecode")
            target = root / "target"
            target.mkdir()

            original_root = self.module.ROOT
            self.module.ROOT = root / "source"
            try:
                self.module._copy_application(target)
            finally:
                self.module.ROOT = original_root

            copied = target / "backend" / "nospoil_nfl"
            self.assertTrue((copied / "handler.py").is_file())
            self.assertFalse((copied / ".DS_Store").exists())
            self.assertFalse((copied / "__pycache__").exists())


if __name__ == "__main__":
    unittest.main()
