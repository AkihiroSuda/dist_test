# Reference materials:
#   https://code.google.com/p/swarming/wiki/IsolateDesign
#   https://code.google.com/p/swarming/wiki/IsolatedDesign
#
# We use the Swarming / Isolate / Luci infrastructure from Chromium.
# We're responsible for generating ".isolate" files, which specify
# the command to run and the environment. Luci takes the isolate files
# and generates ".isolated" files, which can then be submitted
# and executed on a distributed testing cluster.
#`
# Since all the tests share the same dependencies and invocation method,
# we can use "batcharchive" rather than "archive" command
# to archive all the tests at once. Each batcharchive task is parameterized
# with the module's pom.xml and the name of the test to run.
# These parameters are specified in an ".isolated.gen.json" file, one per task,
# and reference a parent .isolate file.
#
# Excerpted from the docs:
#
# A .isolate file is a python file (not JSON!) that contains a single dict
# instance. The allowed items are:
#
#     includes: list of .isolate files to include, i.e. that will be processed
#               before processing this file.
#     variables: dict of variables. Only 3 variables are allowed:
#         command: list that describes the command to run, i.e. each argument.
#         files: list of dependencies to track, i.e. files and directories.
#         read_only: an integer of value 0, 1 or 2. 1 is the default.
#             0 means that the tree is created writeable. Any file can be
#               opened for write and modified.
#             1 means that all the files have the write bit removed (or read
#               only bit set on Windows) so that the file are not writeable
#               without modifying the file mode. This may be or not be
#               enforced by other means.
#             2 means that the directory are not writeable, so that no file
#               can even be added. Enforcement can take different forms but
#               the general concept is the same, no modification, no creation.
#     conditions: list of GYP conditions, so that the content of each
#                 conditions applies only if the condition is True. Each
#                 condition contains a single set of variables.
#
# Each dependency entry can be a file or a directory. If it is a directory,
# it must end with a '/'. Otherwise, it must not end with a '/'. '\' must not
# be used.
import os
import stat
import logging
import pprint
import json

import mavenproject, packager

logger = logging.getLogger(__name__)

class Isolate:

    __RUN_SCRIPT_NAME = """run_test.sh"""

    __COMMAND = """%s <(POM) <(TESTCLASS)""" % __RUN_SCRIPT_NAME

    __ISOLATE_NAME = """disttest.isolate"""

    def __init__(self, project_root, output_dir,
                 include_modules=None, exclude_modules=None, include_patterns=None, exclude_patterns=None,
                 cache_dir=None, extra_deps=None, maven_flags=None, maven_repo=None, verbose=False):
        logger.info("Using output directory " + output_dir)
        self.output_dir = output_dir
        self.maven_project = mavenproject.MavenProject(project_root,
                                                       include_modules=include_modules,
                                                       exclude_modules=exclude_modules,
                                                       include_patterns=include_patterns,
                                                       exclude_patterns=exclude_patterns)
        self.packager = packager.Packager(self.maven_project, self.output_dir,
                                          cache_dir=cache_dir, extra_deps=extra_deps,
                                          maven_flags = maven_flags, maven_repo = maven_repo,
                                          verbose = verbose)
        self.isolated_files = []
        self._maven_flags = maven_flags

    def package(self):
        self.packager.package_all()
        self.packager.write_unpack_script("unpack.sh")

    def _generate_run_script_contents(self):
        contents = """#!/usr/bin/env bash
set -x

[ -z "${AWK}" ] && AWK="$(which gawk 2>/dev/null)" || AWK="$(which awk 2>/dev/null)" || { echo "Error: Could not find AWK tool."; exit 1; }
"${AWK}" 'BEGIN { print strftime("%Y-%m-%d %H:%M:%S"); }' 2>&1 > /dev/null || { echo "Error: your AWK version does not support strftime() function." && exit 1; }

function run() {
    # Add timestamps
    "$@" | "${AWK}" '{ print strftime("%Y-%m-%d %H:%M:%S"), $0; fflush(); }'
    rc=${PIPESTATUS[0]}
    # Exit if non-zero exit code
    if [[ ${rc} != 0 ]]; then
        exit $rc
    fi
}
export JAVA7_BUILD=true
. /opt/toolchain/toolchain.sh
run ./unpack.sh # Generated by packager
source environment.source # Init runtime environment
run which mvn
run mvn -version
run which java
run java -version
"""

        # Write the actual mvn invocation
        contents += "\n"
        contents += "run mvn "

        if self._maven_flags is not None:
            contents += "%s " % self._maven_flags

        contents += """--settings $(pwd)/settings.xml -Dmaven.repo.local=$(pwd)/.m2/repository -Dmaven.artifact.threads=100 surefire:test --file $1 -Dtest=$2 2>&1"""
        return contents

    def generate(self):
        # Write the test runner script
        run_path = os.path.join(self.output_dir, self.__RUN_SCRIPT_NAME)
        with open(run_path, "wt") as out:
            out.write(self._generate_run_script_contents())
        os.chmod(run_path, 0755)

        # Write the parameterized isolate file
        files = self.packager.get_relative_output_paths()
        isolate = {
            'variables': {
                'command': self.__COMMAND.split(" "),
                'files': files,
            },
        }
        isolate_path = os.path.join(self.output_dir, self.__ISOLATE_NAME)
        with open(isolate_path, "wt") as out:
            out.write(str(isolate))

        # Write the per-test json files for isolate's batcharchive command
        num_written = 0
        for module in self.maven_project.modules:
            rel_pom = os.path.relpath(module.pom, self.maven_project.project_root)
            if len(module.test_artifacts) == 0:
                logger.debug("Skipping module with no compiled test-sources jar: %s", module.root)
                continue
            for test in module.test_classes:
                filename = os.path.join(self.output_dir, "%s.isolated.gen.json" % test.name)
                args = ["-i", self.__ISOLATE_NAME, "-s", test.name + ".isolated"]
                extra_args = {
                    "POM" : rel_pom,
                    "TESTCLASS" : test.name,
                }
                for k,v in extra_args.iteritems():
                    args += ["--extra-variable", "%s=%s" % (k,v)]
                gen = {
                    "version" : 1,
                    "dir" : self.output_dir,
                    "args" : args,
                    "name" : test.name,
                }
                with open(filename, "wt") as out:
                    json.dump(gen, out)
                    self.isolated_files.append(filename)
                num_written += 1

        logger.info("Success! Generated %s isolate descriptions in %s", num_written, self.output_dir)