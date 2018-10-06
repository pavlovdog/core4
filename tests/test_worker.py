# -*- coding: utf-8 -*-

import sys
import ctypes

import datetime
import pandas as pd
import psutil
import threading
import time

import core4.base.main
import core4.logger.mixin
import core4.queue.job
import core4.queue.main
import core4.queue.worker
from tests.pytest_util import *

LOOP_INTERVAL = 0.25
libc = ctypes.CDLL(None)


def setup_thread_excepthook():
    """
    Workaround for `sys.excepthook` thread bug from:
    http://bugs.python.org/issue1230540

    Call once from the main thread before creating any threads.
    """

    init_original = threading.Thread.__init__

    def init(self, *args, **kwargs):

        init_original(self, *args, **kwargs)
        run_original = self.run

        def run_with_except_hook(*args2, **kwargs2):
            try:
                run_original(*args2, **kwargs2)
            except Exception:
                sys.excepthook(*sys.exc_info())

        self.run = run_with_except_hook

    threading.Thread.__init__ = init


setup_thread_excepthook()


@pytest.fixture(autouse=True)
def worker_timing():
    os.environ["CORE4_OPTION_" \
               "worker__execution_plan__work_jobs"] = "!!float {}".format(
        LOOP_INTERVAL)
    os.environ["CORE4_OPTION_" \
               "worker__execution_plan__flag_jobs"] = "!!float 3"


@pytest.mark.timeout(30)
def test_register(caplog):
    pool = []
    workers = []
    for i in range(1, 4):
        worker = core4.queue.worker.CoreWorker(name="worker-{}".format(i))
        workers.append(worker)
        t = threading.Thread(target=worker.start, args=())
        t.start()
        pool.append(t)
    wait = 3.
    time.sleep(wait)
    for w in workers:
        w.exit = True
    for t in pool:
        t.join()
    for worker in workers:
        assert [i["interval"]
                for i in worker.plan
                if i["name"] == "work_jobs"] == [LOOP_INTERVAL]
        assert [wait / i["interval"]
                for i in worker.plan
                if i["name"] == "work_jobs"][0] >= worker.cycle["total"]


def test_register_duplicate():
    w1 = core4.queue.worker.CoreWorker(name="worker")
    w1.register_worker()
    w2 = core4.queue.worker.CoreWorker(name="worker")
    w1.register_worker()


def test_plan():
    worker = core4.queue.worker.CoreWorker()
    assert len(worker.create_plan()) == 4


@pytest.mark.timeout(30)
def test_5loops():
    worker = core4.queue.worker.CoreWorker()
    t = threading.Thread(target=worker.start, args=())
    t.start()
    while worker.cycle["total"] < 5:
        time.sleep(0.1)
    worker.exit = True
    t.join()


@pytest.mark.timeout(30)
def test_setup():
    worker = core4.queue.worker.CoreWorker()
    worker.exit = True
    worker.start()


@pytest.mark.timeout(30)
def test_maintenance():
    queue = core4.queue.main.CoreQueue()
    queue.enter_maintenance()
    worker = core4.queue.worker.CoreWorker()
    t = threading.Thread(target=worker.start, args=())
    t.start()
    while worker.cycle["total"] < 3:
        time.sleep(0.5)
    worker.exit = True
    assert worker.at is None
    t.join()
    assert worker.cycle == {
        'collect_stats': 0, 'total': 3, 'work_jobs': 0,
        'flag_jobs': 0, 'remove_jobs': 0}


@pytest.mark.timeout(30)
def test_halt():
    queue = core4.queue.main.CoreQueue()
    queue.halt(now=True)
    time.sleep(2)
    worker = core4.queue.worker.CoreWorker()
    t = threading.Thread(target=worker.start, args=())
    t.start()
    while worker.cycle["total"] < 3:
        time.sleep(0.5)
    queue.halt(now=True)
    t.join()


def test_enqueue_dequeue(queue):
    enqueued_job = queue.enqueue(core4.queue.job.DummyJob)
    worker = core4.queue.worker.CoreWorker()
    doc = worker.get_next_job()
    dequeued_job = queue.job_factory(doc["name"]).deserialise(**doc)
    assert enqueued_job.__dict__.keys() == dequeued_job.__dict__.keys()
    for k in enqueued_job.__dict__.keys():
        if k not in ("logger", "config", "class_config"):
            if enqueued_job.__dict__[k] != dequeued_job.__dict__[k]:
                assert enqueued_job.__dict__[k] == dequeued_job.__dict__[k]


def test_offset():
    queue = core4.queue.main.CoreQueue()
    enqueued_id = []
    for i in range(0, 5):
        enqueued_id.append(queue.enqueue(core4.queue.job.DummyJob, i=i)._id)
    worker = core4.queue.worker.CoreWorker()
    dequeued_id = []
    dequeued_id.append(worker.get_next_job()["_id"])
    dequeued_id.append(worker.get_next_job()["_id"])
    dequeued_id.append(worker.get_next_job()["_id"])
    enqueued_job = queue.enqueue(core4.queue.job.DummyJob, i=5, priority=10)
    dequeued_job = worker.get_next_job()
    assert enqueued_job._id == dequeued_job["_id"]
    assert enqueued_id[0:len(dequeued_id)] == dequeued_id


def test_lock():
    queue = core4.queue.main.CoreQueue()
    worker = core4.queue.worker.CoreWorker()
    queue.enqueue(core4.queue.job.DummyJob)
    job = worker.get_next_job()
    assert queue.lock_job(job["_id"], worker.identifier)
    assert queue.lock_job(job["_id"], worker.identifier) is False


def test_remove(mongodb):
    queue = core4.queue.main.CoreQueue()
    worker = core4.queue.worker.CoreWorker()
    _id = queue.enqueue(core4.queue.job.DummyJob)._id
    assert _id is not None
    assert queue.remove_job(_id)
    job = worker.get_next_job()
    assert job is None
    worker.remove_jobs()
    assert 0 == mongodb.core4test.sys.queue.count()
    assert 1 == mongodb.core4test.sys.journal.count()
    worker.cleanup()
    assert 0 == mongodb.core4test.sys.lock.count()


@pytest.mark.timeout(30)
def test_removing():
    queue = core4.queue.main.CoreQueue()
    pool = []
    workers = []
    count = 50
    for i in range(0, count):
        job = queue.enqueue(core4.queue.job.DummyJob, i=i)
        queue.remove_job(job._id)
    for i in range(1, 4):
        worker = core4.queue.worker.CoreWorker(name="worker-{}".format(i))
        workers.append(worker)
        t = threading.Thread(target=worker.start, args=())
        t.start()
        pool.append(t)
    while queue.config.sys.queue.count() > 0:
        time.sleep(1)
    for w in workers:
        w.exit = True
    for t in pool:
        t.join()
    assert queue.config.sys.queue.count() == 0
    assert queue.config.sys.journal.count() == count


@pytest.mark.timeout(30)
def test_start_job():
    queue = core4.queue.main.CoreQueue()
    worker = core4.queue.worker.CoreWorker()
    worker.cleanup()
    job = queue.enqueue(core4.queue.job.DummyJob)
    assert job.identifier == job._id
    assert job._id is not None
    assert job.wall_time is None
    worker.start_job(job)
    while queue.config.sys.queue.count() > 0:
        time.sleep(0.5)
    assert queue.config.sys.queue.count() == 0
    assert queue.config.sys.journal.count() == 1
    job = queue.find_job(job._id)
    assert job.state == "complete"
    print(job.started_at)
    print(job.finished_at)
    print(job.finished_at - job.started_at)
    print(job.runtime)
    import pandas as pd
    data = list(queue.config.sys.log.find())
    df = pd.DataFrame(data)
    print(df.to_string())


@pytest.mark.timeout(30)
def test_start_job2(queue):
    threads = 3
    pool = []
    workers = []
    count = 5
    for i in range(0, count):
        queue.enqueue(core4.queue.job.DummyJob, i=i)
    for i in range(0, threads):
        worker = core4.queue.worker.CoreWorker(name="worker-{}".format(i + 1))
        workers.append(worker)
        t = threading.Thread(target=worker.start, args=())
        t.start()
        pool.append(t)
    while queue.config.sys.queue.count() > 0:
        time.sleep(1)
    for w in workers:
        w.exit = True
    for t in pool:
        t.join()

    import pandas as pd
    data = list(queue.config.sys.log.find())
    df = pd.DataFrame(data)
    df.to_pickle('/tmp/df.pickle')
    print(df[df.level.isin(["WARNING", "ERROR", "CRITICAL"])].to_string())


@pytest.fixture
def queue():
    return core4.queue.main.CoreQueue()


class WorkerHelper:
    def __init__(self):
        self.queue = core4.queue.main.CoreQueue()
        self.pool = []
        self.worker = []

    def start(self, num=3):
        for i in range(0, num):
            worker = core4.queue.worker.CoreWorker(
                name="worker-{}".format(i + 1))
            self.worker.append(worker)
            t = threading.Thread(target=worker.start, args=())
            self.pool.append(t)
        for t in self.pool:
            t.start()

    def stop(self):
        for worker in self.worker:
            worker.exit = True
        for t in self.pool:
            t.join()

    def wait_queue(self):
        while self.queue.config.sys.queue.count() > 0:
            time.sleep(1)
        self.stop()


@pytest.fixture
def worker():
    return WorkerHelper()


@pytest.mark.timeout(30)
def test_ok(queue, worker):
    queue.enqueue(core4.queue.job.DummyJob)
    worker.start(1)
    worker.wait_queue()


@pytest.mark.timeout(30)
def test_error(queue, worker):
    import project.work
    queue.enqueue(project.work.ErrorJob)
    worker.start(1)
    while queue.config.sys.queue.count() > 0:
        time.sleep(1)
        if queue.config.sys.queue.count({"state": "error"}) > 0:
            break
    worker.stop()
    df = pd.DataFrame(list(queue.config.sys.log.find()))
    df.to_pickle('/tmp/df.pickle')
    assert df[df.message.str.find("done execution") >= 0].shape[0] == 3
    assert df[df.message.str.find("start execution") >= 0].shape[0] == 3
    x = pd.to_timedelta(
        df[((df.message.str.find("execution") >= 0) & (df.level == "INFO"))
        ].created.diff()).apply(lambda r: r.total_seconds()).tolist()
    assert [x[i] >= 5 for i in [1, 2]] == [True, True]


@pytest.mark.timeout(30)
def test_success_after_failure(queue, worker):
    import project.work
    queue.enqueue(project.work.ErrorJob, success=True)
    worker.start(1)
    worker.wait_queue()
    worker.stop()
    df = pd.DataFrame(list(queue.config.sys.log.find()))
    assert df[df.message.str.find("start execution") >= 0].shape[0] == 3
    assert df[df.message.str.find(
        "done execution with [failed]") >= 0].shape[0] == 2
    assert df[df.message.str.find(
        "done execution with [complete]") >= 0].shape[0] == 1


@pytest.mark.timeout(90)
def test_defer(queue, worker):
    import project.work
    queue.enqueue(project.work.DeferJob)
    worker.start(1)
    while queue.config.sys.queue.count() > 0:
        time.sleep(1)
        if queue.config.sys.queue.count({"state": "inactive"}) > 0:
            break
    worker.stop()
    df = pd.DataFrame(list(queue.config.sys.log.find()))
    assert df[df.message.str.find(
        "done execution with [deferred]") >= 0].shape[0] > 2
    assert df[df.message.str.find(
        "done execution with [inactive]") >= 0].shape[0] == 1


@pytest.mark.timeout(90)
def test_mass_defer(queue, worker, mongodb):
    import project.work
    for i in range(0, 10):
        queue.enqueue(project.work.DeferJob, i=i, success=True, defer_max=5)
    worker.start(4)
    while queue.config.sys.queue.count() > 0:
        time.sleep(1)
        if queue.config.sys.queue.count({"state": "inactive"}) == 10:
            break
    worker.stop()


@pytest.mark.timeout(30)
def test_fail2inactive(queue, worker, mongodb):
    import project.work
    queue.enqueue(project.work.ErrorJob, defer_max=15, attempts=5)
    worker.start(1)
    while queue.config.sys.queue.count() > 0:
        time.sleep(1)
        if queue.config.sys.queue.count({"state": "inactive"}) == 1:
            break
    worker.stop()


@pytest.mark.timeout(30)
def test_remove_failed(queue, worker, mongodb):
    import project.work
    job = queue.enqueue(project.work.ErrorJob, attempts=5, sleep=10)
    worker.start(1)
    while queue.config.sys.queue.count({"state": "running"}) == 0:
        time.sleep(0.25)
    assert queue.remove_job(job._id)
    while queue.config.sys.queue.count() > 0:
        time.sleep(0.25)
    worker.stop()
    assert queue.config.sys.journal.count() == 1
    assert queue.config.sys.queue.count() == 0


@pytest.mark.timeout(30)
def test_remove_deferred(queue, worker, mongodb):
    import project.work
    job = queue.enqueue(project.work.DeferJob, defer_time=10)
    worker.start(1)
    while queue.config.sys.queue.count({"state": "deferred"}) == 0:
        time.sleep(0.25)
    assert queue.remove_job(job._id)
    while queue.config.sys.queue.count() > 0:
        time.sleep(0.25)
    worker.stop()
    assert queue.config.sys.journal.count() == 1
    assert queue.config.sys.queue.count() == 0


@pytest.mark.timeout(30)
def test_remove_complete(queue, worker, mongodb):
    job = queue.enqueue(core4.queue.job.DummyJob, sleep=10)
    worker.start(1)
    while queue.config.sys.queue.count({"state": "running"}) == 0:
        time.sleep(0.25)
    assert queue.remove_job(job._id)
    while queue.config.sys.queue.count() > 0:
        time.sleep(0.25)
    worker.stop()
    assert queue.config.sys.journal.count() == 1
    assert queue.config.sys.queue.count() == 0
    job = queue.find_job(job._id)
    assert job.state == "complete"


@pytest.mark.timeout(90)
def test_remove_inactive(queue, worker):
    import project.work
    job = queue.enqueue(project.work.DeferJob)
    worker.start(1)
    while queue.config.sys.queue.count() > 0:
        time.sleep(1)
        if queue.config.sys.queue.count({"state": "inactive"}) > 0:
            break
    assert queue.remove_job(job._id)
    while queue.config.sys.queue.count() > 0:
        time.sleep(1)
    worker.stop()
    assert queue.config.sys.journal.count() == 1
    assert queue.config.sys.queue.count() == 0
    job = queue.find_job(job._id)
    assert job.state == "inactive"


@pytest.mark.timeout(30)
def test_remove_error(queue, worker):
    import project.work
    job = queue.enqueue(project.work.ErrorJob)
    worker.start(1)
    while queue.config.sys.queue.count() > 0:
        time.sleep(1)
        if queue.config.sys.queue.count({"state": "error"}) > 0:
            break
    assert queue.remove_job(job._id)
    while queue.config.sys.queue.count() > 0:
        time.sleep(1)
    worker.stop()
    assert queue.config.sys.journal.count() == 1
    assert queue.config.sys.queue.count() == 0
    job = queue.find_job(job._id)
    assert job.state == "error"


@pytest.mark.timeout(30)
def test_nonstop(queue, worker):
    job = queue.enqueue(core4.queue.job.DummyJob, sleep=10, wall_time=5)
    worker.start(1)
    while queue.config.sys.queue.count() > 0:
        time.sleep(1)
        if queue.config.sys.queue.count({"wall_at": {"$ne": None}}) > 0:
            break
    while queue.config.sys.queue.count() > 0:
        time.sleep(1)
    worker.stop()
    assert queue.config.sys.journal.count() == 1
    assert queue.config.sys.queue.count() == 0
    job = queue.find_job(job._id)
    assert job.wall_at is not None
    df = pd.DataFrame(list(queue.config.sys.log.find()))
    assert df[df.message.str.find(
        "successfully set non-stop job [{}]".format(
            job._id)) >= 0].shape[0] == 1


class ProgressJob(core4.queue.job.CoreJob):
    author = "mra"
    progress_interval = 10

    def execute(self, *args, **kwargs):
        runtime = 5.
        tx = core4.util.now() + datetime.timedelta(seconds=runtime)
        n = 0
        while True:
            n += 1
            t0 = core4.util.now()
            if t0 >= tx:
                break
            p = 1. - (tx - t0).total_seconds() / runtime
            self.progress(p, "at %d", n)
            time.sleep(0.25)


@pytest.mark.timeout(30)
def test_progress1(queue, worker):
    queue.enqueue(ProgressJob)
    worker.start(1)
    worker.wait_queue()
    worker.stop()
    df = pd.DataFrame(list(queue.config.sys.log.find()))
    assert df[
               ((df.message.str.find("progress") >= 0) & (df.level == "DEBUG"))
           ].shape[0] == 2


@pytest.mark.timeout(30)
def test_progress2(queue, worker):
    queue.enqueue(ProgressJob, progress_interval=1)
    worker.start(1)
    worker.wait_queue()
    worker.stop()
    df = pd.DataFrame(list(queue.config.sys.log.find()))
    assert df[
               ((df.message.str.find("progress") >= 0) & (df.level == "DEBUG"))
           ].shape[0] >= 5


class NoProgressJob(core4.queue.job.CoreJob):
    author = "mra"

    def execute(self, *args, **kwargs):
        time.sleep(5)


@pytest.mark.timeout(30)
def test_zombie(queue, worker):
    job = queue.enqueue(NoProgressJob, zombie_time=2)
    worker.start(1)
    worker.wait_queue()
    worker.stop()
    assert queue.config.sys.journal.count() == 1
    assert queue.config.sys.queue.count() == 0
    job = queue.find_job(job._id)
    assert job.zombie_at is not None
    df = pd.DataFrame(list(queue.config.sys.log.find()))
    assert df[df.message.str.find(
        "successfully set zombie job [{}]".format(
            job._id)) >= 0].shape[0] == 1


class ForeverJob(core4.queue.job.CoreJob):
    author = "mra"

    def execute(self, *args, **kwargs):
        time.sleep(60 * 60 * 24)


@pytest.mark.timeout(30)
def test_no_pid(queue, worker):
    job = queue.enqueue(ForeverJob)
    worker.start(1)
    while True:
        job = queue.find_job(job._id)
        if job.locked and job.locked["pid"]:
            job = queue.find_job(job._id)
            proc = psutil.Process(job.locked["pid"])
            time.sleep(5)
            proc.kill()
            break
    while True:
        job = queue.find_job(job._id)
        if job.state == "killed":
            break
    worker.stop()


@pytest.mark.timeout(30)
def test_kill(queue, worker):
    job = queue.enqueue(ForeverJob, zombie_time=2)
    worker.start(1)
    while True:
        job = queue.find_job(job._id)
        if job.locked and job.locked["pid"]:
            break
    queue.kill_job(job._id)
    while True:
        job = queue.find_job(job._id)
        if job.state == "killed":
            break
    queue.remove_job(job._id)
    worker.wait_queue()
    assert queue.config.sys.journal.count() == 1
    assert queue.config.sys.queue.count() == 0
    job = queue.find_job(job._id)
    assert job.state == "killed"
    assert job.killed_at is not None
    worker.stop()


class RestartDeferredTest(core4.queue.job.CoreJob):
    author = 'mra'
    defer_time = 120

    def execute(self, *args, **kwargs):
        if self.trial == 2:
            return
        self.defer("expected deferred")


@pytest.mark.timeout(30)
def test_restart_deferred(queue, worker):
    job = queue.enqueue(RestartDeferredTest)
    worker.start(1)
    while True:
        j = queue.find_job(job._id)
        if j.state == "deferred":
            break
    queue.restart_job(job._id)
    worker.wait_queue()
    worker.stop()
    assert queue.config.sys.journal.count() == 1
    assert queue.config.sys.queue.count() == 0
    job = queue.find_job(job._id)
    assert job.trial == 2
    assert job.state == core4.queue.job.STATE_COMPLETE


class RestartFailedTest(core4.queue.job.CoreJob):
    author = 'mra'
    error_time = 120
    attempts = 2

    def execute(self, *args, **kwargs):
        if self.trial == 2:
            return
        raise RuntimeError("expected failure")


@pytest.mark.timeout(30)
def test_failed_deferred(queue, worker):
    job = queue.enqueue(RestartFailedTest)
    worker.start(1)
    while True:
        j = queue.find_job(job._id)
        if j.state == "failed":
            break
    queue.restart_job(job._id)
    worker.wait_queue()
    worker.stop()
    assert queue.config.sys.journal.count() == 1
    assert queue.config.sys.queue.count() == 0
    job = queue.find_job(job._id)
    assert job.trial == 2
    assert job.state == core4.queue.job.STATE_COMPLETE


class RestartErrorTest(core4.queue.job.CoreJob):
    author = 'mra'
    error_time = 120
    attempts = 1

    def execute(self, *args, **kwargs):
        if self.enqueued["parent_id"] is not None:
            return
        raise RuntimeError("expected failure")


@pytest.mark.timeout(30)
def test_restart_error(queue, worker):
    job = queue.enqueue(RestartErrorTest)
    worker.start(1)
    while True:
        j = queue.find_job(job._id)
        if j.state == "error":
            break
    new_id = queue.restart_job(job._id)
    worker.wait_queue()
    worker.stop()
    assert queue.config.sys.journal.count() == 2
    assert queue.config.sys.queue.count() == 0
    parent = queue.find_job(job._id)
    assert parent.state == core4.queue.job.STATE_ERROR
    child = queue.find_job(new_id)
    assert child.state == core4.queue.job.STATE_COMPLETE


def test_kill_running_only(queue):
    job = queue.enqueue(core4.queue.job.DummyJob)
    assert not queue.kill_job(job._id)


class RequiresArgTest(core4.queue.job.CoreJob):
    author = 'mra'

    def execute(self, test, *args, **kwargs):
        pass


def test_requires_arg(queue, worker):
    job = queue.enqueue(RequiresArgTest)
    worker.start(1)
    while True:
        j = queue.find_job(job._id)
        if j.state == "error":
            break
    worker.stop()


class RestartKilledTest(core4.queue.job.CoreJob):
    author = 'mra'
    defer_time = 1

    def execute(self, *args, **kwargs):
        if self.enqueued["parent_id"]:
            return
        time.sleep(120)


@pytest.mark.timeout(30)
def test_restart_killed(queue, worker):
    job = queue.enqueue(RestartKilledTest)
    worker.start(1)
    while True:
        j = queue.find_job(job._id)
        if j.state == "running":
            break
    queue.kill_job(job._id)
    while True:
        j = queue.find_job(job._id)
        if j.state == "killed":
            break
    new_id = queue.restart_job(job._id)
    queue.restart_job(new_id)
    # queue.remove_job(job._id)
    worker.wait_queue()
    worker.stop()


class RestartInactiveTest(core4.queue.job.CoreJob):
    author = 'mra'
    defer_max = 5
    defer_time = 1

    def execute(self, *args, **kwargs):
        if self.enqueued["parent_id"]:
            return
        self.defer("expected defer")


@pytest.mark.timeout(30)
def test_restart_inactive(queue, worker):
    job = queue.enqueue(RestartInactiveTest)
    worker.start(1)
    while True:
        j = queue.find_job(job._id)
        if j.state == "inactive":
            break
    queue.restart_job(job._id)
    worker.wait_queue()
    worker.stop()


class OutputTestJob(core4.queue.job.CoreJob):
    author = 'mra'

    def execute(self, *args, **kwargs):
        print("this output comes from %s" % self.qual_name())
        os.system("echo this comes from echo")
        os.system("echo this comes from stderr > /dev/stderr")
        libc.puts(b"this comes from C")

def test_stdout(queue, worker):
    job = queue.enqueue(OutputTestJob)
    worker.start(3)
    worker.wait_queue()
    worker.stop()

# last_error
# job turns inactive
# query_at mit defer
# remove jobs
# remove inactive
# job turns nonstop (wall_time)
# job turns into zombie
# custom progress_interval, by DEFAULT, by job
# auto kill if PID was gone
# killed
# remove killed
# restarting
# last_runtime in cookie
# check all exceptions have logging and log exceptions
# capture stdout and stderr

# todo: project maintenance
# todo: job collection, access management
# todo: dependency and chain
# todo: max_parallel
# todo: memory logger
# todo: stats
