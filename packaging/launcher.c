/*
 * The application's executable.
 *
 * Its whole job is to start the bundled Python quietly and get out of the way.
 * It deliberately exits straight after starting the server, so that macOS does
 * not consider the application "open". That way every double-click of the icon
 * runs this again, and the server it finds already running simply reopens the
 * page in the browser rather than starting a second copy.
 *
 * Nothing here opens a window, and nothing here writes to a terminal.
 */

#include <errno.h>
#include <fcntl.h>
#include <libgen.h>
#include <mach-o/dyld.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define PATH_LIMIT 4096

static void join(char *out, size_t n, const char *a, const char *b) {
    snprintf(out, n, "%s/%s", a, b);
}

int main(int argc, char **argv) {
    (void)argc;
    (void)argv;

    char exec_path[PATH_LIMIT];
    uint32_t size = sizeof(exec_path);
    if (_NSGetExecutablePath(exec_path, &size) != 0) {
        return 1;
    }

    char resolved[PATH_LIMIT];
    if (realpath(exec_path, resolved) == NULL) {
        snprintf(resolved, sizeof(resolved), "%s", exec_path);
    }

    /* .../Contents/MacOS/AuthoringAssistant -> .../Contents/Resources */
    char macos_copy[PATH_LIMIT];
    snprintf(macos_copy, sizeof(macos_copy), "%s", resolved);
    char *macos_dir = dirname(macos_copy);

    char contents_copy[PATH_LIMIT];
    snprintf(contents_copy, sizeof(contents_copy), "%s", macos_dir);
    char *contents_dir = dirname(contents_copy);

    char resources[PATH_LIMIT];
    join(resources, sizeof(resources), contents_dir, "Resources");

    char python_home[PATH_LIMIT];
    join(python_home, sizeof(python_home), resources, "python");

    char python_bin[PATH_LIMIT];
    join(python_bin, sizeof(python_bin), python_home, "bin/AuthoringAssistant");

    char script[PATH_LIMIT];
    join(script, sizeof(script), resources, "launch.py");

    /* Let go of the parent so nothing lingers on screen or in the Dock. */
    pid_t pid = fork();
    if (pid < 0) {
        return 1;
    }
    if (pid > 0) {
        return 0;
    }
    setsid();

    /* There is no window to print to, so anything printed goes to a log file. */
    const char *home = getenv("HOME");
    if (home != NULL) {
        char dir[PATH_LIMIT];
        snprintf(dir, sizeof(dir), "%s/Library/Application Support", home);
        mkdir(dir, 0755);
        snprintf(dir, sizeof(dir),
                 "%s/Library/Application Support/Authoring Assistant", home);
        mkdir(dir, 0700);

        char log_path[PATH_LIMIT];
        join(log_path, sizeof(log_path), dir, "log.txt");
        int fd = open(log_path, O_WRONLY | O_CREAT | O_APPEND, 0600);
        if (fd >= 0) {
            dup2(fd, STDOUT_FILENO);
            dup2(fd, STDERR_FILENO);
            if (fd > STDERR_FILENO) {
                close(fd);
            }
        }
    }
    int devnull = open("/dev/null", O_RDONLY);
    if (devnull >= 0) {
        dup2(devnull, STDIN_FILENO);
        if (devnull > STDERR_FILENO) {
            close(devnull);
        }
    }

    setenv("PYTHONHOME", python_home, 1);
    setenv("PYTHONDONTWRITEBYTECODE", "1", 1);
    setenv("PYTHONNOUSERSITE", "1", 1);
    unsetenv("PYTHONPATH");
    unsetenv("PYTHONSTARTUP");

    if (chdir(resources) != 0) {
        fprintf(stderr, "launcher: cannot enter %s: %s\n",
                resources, strerror(errno));
    }

    char *const args[] = {python_bin, "-s", "-B", script, NULL};
    execv(python_bin, args);

    fprintf(stderr, "launcher: could not start %s: %s\n",
            python_bin, strerror(errno));
    return 1;
}
