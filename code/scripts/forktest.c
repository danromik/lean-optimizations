#include <lean/lean.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/wait.h>
#include <signal.h>
extern void lean_initialize_runtime_module(void);

static lean_object * task_fn(lean_object * u) {
    (void)u;
    return lean_box(7);
}

static lean_object * spawn_one(void) {
    lean_object * c = lean_alloc_closure((void*)task_fn, 1, 0);
    return lean_task_spawn_core(c, 0 /* prio */, false);
}

int main(int argc, char ** argv) {
    int mode = (argc > 1) ? atoi(argv[1]) : 0;
    unsigned childprio = (argc > 2) ? (unsigned)atoi(argv[2]) : 0;
    lean_initialize_runtime_module();
    lean_init_task_manager_using(4);

    if (mode == 0) {
        /* Warm the pool: run a task so a worker thread is created and then parks idle. */
        lean_object * t = spawn_one();
        lean_object * v = lean_task_get(t);
        fprintf(stderr, "parent: warmup task returned %d\n", (int)lean_unbox(v));
        lean_dec(t);
        usleep(200000); /* let the worker park in m_queue_cv.wait */
    }
    fflush(stderr);

    pid_t pid = fork();
    if (pid == 0) {
        fprintf(stderr, "child: alive, spawning task at prio=%u...\n", childprio); fflush(stderr);
        lean_object * t = lean_task_spawn_core(lean_alloc_closure((void*)task_fn, 1, 0), childprio, false);
        fprintf(stderr, "child: task enqueued, calling lean_task_get...\n"); fflush(stderr);
        lean_object * v = lean_task_get(t);
        fprintf(stderr, "child: GOT %d  -- NO DEADLOCK\n", (int)lean_unbox(v)); fflush(stderr);
        _exit(0);
    }
    int status = 0;
    for (int i = 0; i < 50; i++) {
        pid_t r = waitpid(pid, &status, WNOHANG);
        if (r == pid) { fprintf(stderr, "parent: child exited status=%d\n", status); return 0; }
        usleep(100000);
    }
    fprintf(stderr, "parent: TIMEOUT after 5s -- child is HUNG\n");
    kill(pid, 9);
    return 1;
}
