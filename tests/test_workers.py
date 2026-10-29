import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Event, Lock
from unittest.mock import Mock, patch

from evaluator import execution_gate, run
from evaluator.__main__ import main
from evaluator.metrics import load_jsonl
from evaluator.workers import completed_jobs


class ParallelWorkerTests(unittest.TestCase):
    def test_completed_job_does_not_wait_for_earlier_slow_job(self):
        release_first = Event()

        def work(job):
            if job == 1 and not release_first.wait(timeout=3):
                raise TimeoutError("slow job was not released")
            return job

        jobs = completed_jobs(work, [1, 2], 2)
        try:
            job, future = next(jobs)
            self.assertEqual(job, 2)
            self.assertEqual(future.result(), 2)
            release_first.set()
            remaining = [(job, future.result()) for job, future in jobs]
            self.assertEqual(remaining, [(1, 1)])
        finally:
            release_first.set()
            jobs.close()

    def test_bound_and_real_overlap(self):
        barrier = Barrier(3)
        lock = Lock()
        active = peak = 0

        def work(job):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            barrier.wait(timeout=3)
            with lock:
                active -= 1
            return job * 2

        rows = list(completed_jobs(work, range(9), 3))
        self.assertEqual(peak, 3)
        self.assertEqual({job: future.result() for job, future in rows},
                         {job: job * 2 for job in range(9)})

    def test_worker_exception_is_not_silently_discarded(self):
        def fail(job):
            raise RuntimeError("worker failed")

        for _, future in completed_jobs(fail, [1], 1):
            with self.assertRaisesRegex(RuntimeError, "worker failed"):
                future.result()

    def test_wait_callback_and_no_per_worker_heartbeat(self):
        from concurrent.futures import wait
        first = True

        def simulated_wait(*args, **kwargs):
            nonlocal first
            if first:
                first = False
                return set(), set(args[0])
            return wait(*args, **kwargs)

        heartbeat = Mock()
        with patch("evaluator.workers.wait", side_effect=simulated_wait):
            results = list(completed_jobs(lambda job: job, [1, 2], 2, on_wait=heartbeat))
        heartbeat.assert_called_once_with()
        self.assertEqual(len(results), 2)

    def test_invalid_cli_workers_before_calls(self):
        for entry in (main, run.main, execution_gate.main):
            for value in ("0", "-2", "1.5", "bad"):
                with self.subTest(entry=entry, value=value), redirect_stdout(io.StringIO()), patch(
                    "sys.stderr", new_callable=io.StringIO
                ), self.assertRaises(SystemExit) as exc:
                    entry(["--workers", value])
                self.assertEqual(exc.exception.code, 2)
        for value in (0, -1, True):
            with self.assertRaises(ValueError):
                list(completed_jobs(lambda job: job, [1], value))

    def test_both_runners_overlap_preserve_records_repeats_and_failures(self):
        for suite in ("intent", "execution"):
            for workers in (1, 3):
                with self.subTest(suite=suite, workers=workers):
                    if suite == "intent":
                        schema = json.loads(run.SCHEMA_PATH.read_text(encoding="utf-8"))
                        cases = run._load_cases(run.DATASET_PATH, schema)[:3]
                    else:
                        _, _, schema = execution_gate.load_setup()
                        cases = execution_gate.load_cases(execution_gate.DATASET_PATH, schema)[:3]
                    barrier = Barrier(workers)
                    lock = Lock()
                    active = peak = 0
                    expected = {case["expected"]["test_case_id"]: case["expected"] for case in cases}
                    failed_id = cases[1]["expected"]["test_case_id"]

                    def response(case_id):
                        nonlocal active, peak
                        with lock:
                            active += 1
                            peak = max(peak, active)
                        barrier.wait(timeout=3)
                        with lock:
                            active -= 1
                        if case_id == failed_id:
                            raise TimeoutError("simulated failure")
                        return json.dumps(expected[case_id]), 10, 2, None

                    def intent_predict(client, deployment, question, case_id, schema, **kwargs):
                        return response(case_id)

                    def execution_predict(client, deployment, case, *args):
                        return response(case["expected"]["test_case_id"])

                    config = {"deployment_names": ["test-model"], "repeat_count": 2}
                    with TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
                        if suite == "intent":
                            with patch("evaluator.run._predict", side_effect=intent_predict):
                                path, summary_path, failures = run.run(
                                    config, cases, schema, Mock(), Path(folder), workers=workers,
                                )
                        else:
                            with patch("evaluator.execution_gate.predict", side_effect=execution_predict):
                                path, summary_path, failures = execution_gate.run(
                                    config, cases, {"test-model": Mock()}, Path(folder), workers=workers,
                                )
                        rows = load_jsonl(path)
                        summary = json.loads(summary_path.read_text(encoding="utf-8"))["test-model"]
                    self.assertEqual(peak, workers)
                    self.assertEqual(failures, 2)
                    self.assertEqual(len(rows), 6)
                    self.assertTrue(all(row["workers"] == workers for row in rows))
                    self.assertEqual(len({(r["expected"]["test_case_id"], r["repeat_index"])
                                          for r in rows}), 6)
                    self.assertEqual(sum(r["error"] is not None for r in rows), 2)
                    self.assertEqual(summary["tokens"]["input_total"], 40)
                    metric = "intent" if suite == "intent" else "decision"
                    self.assertAlmostEqual(summary[metric]["accuracy"], 4 / 6)


if __name__ == "__main__":
    unittest.main()
