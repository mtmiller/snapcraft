# -*- Mode:Python; indent-tabs-mode:nil; tab-width:4 -*-
#
# Copyright (C) 2015-2018 Canonical Ltd
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os
import subprocess
import sys
import tempfile
from textwrap import dedent
from unittest import mock

import fixtures
from testtools.matchers import Contains, Equals, GreaterThan, Not

import snapcraft
from snapcraft.internal import common
from tests.fixture_setup.os_release import FakeOsRelease

from . import ProjectLoaderBaseTest


class EnvironmentTest(ProjectLoaderBaseTest):
    def setUp(self):
        super().setUp()

        self.snapcraft_yaml = dedent(
            """\
            name: test
            base: core18
            version: "1"
            summary: test
            description: test
            confinement: strict
            grade: stable

            parts:
              part1:
                plugin: nil
        """
        )

    def test_config_snap_environment(self):
        project_config = self.make_snapcraft_project(self.snapcraft_yaml)

        lib_paths = [
            os.path.join(self.prime_dir, "lib"),
            os.path.join(self.prime_dir, "usr", "lib"),
        ]
        for lib_path in lib_paths:
            os.makedirs(lib_path)

        environment = project_config.snap_env()
        self.assertThat(
            environment,
            Contains(
                'PATH="{0}/usr/sbin:{0}/usr/bin:{0}/sbin:{0}/bin${{PATH:+:$PATH}}"'.format(
                    self.prime_dir
                )
            ),
        )
        self.assertThat(
            environment,
            Contains(
                'LD_LIBRARY_PATH="${{LD_LIBRARY_PATH:+$LD_LIBRARY_PATH:}}'
                '{0}/lib:{0}/usr/lib"'.format(self.prime_dir)
            ),
        )

    def test_config_snap_environment_with_no_library_paths(self):
        project_config = self.make_snapcraft_project(self.snapcraft_yaml)

        environment = project_config.snap_env()
        self.assertTrue(
            'PATH="{0}/usr/sbin:{0}/usr/bin:{0}/sbin:{0}/bin${{PATH:+:$PATH}}"'.format(
                self.prime_dir
            )
            in environment,
            "Current PATH is {!r}".format(environment),
        )
        for e in environment:
            self.assertFalse(
                "LD_LIBRARY_PATH" in e, "Current environment is {!r}".format(e)
            )

    @mock.patch.object(
        snapcraft.internal.pluginhandler.PluginHandler, "get_primed_dependency_paths"
    )
    def test_config_snap_environment_with_dependencies(self, mock_get_dependencies):
        library_paths = {
            os.path.join(self.prime_dir, "lib1"),
            os.path.join(self.prime_dir, "lib2"),
        }
        mock_get_dependencies.return_value = library_paths
        project_config = self.make_snapcraft_project(self.snapcraft_yaml)

        for lib_path in library_paths:
            os.makedirs(lib_path)

        # Ensure that LD_LIBRARY_PATH is present and it contains the
        # extra dependency paths.
        self.assertThat(
            project_config.snap_env(),
            Contains(
                'LD_LIBRARY_PATH="{0}/lib1:{0}/lib2${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}"'.format(
                    self.prime_dir
                )
            ),
        )

    @mock.patch.object(
        snapcraft.internal.pluginhandler.PluginHandler, "get_primed_dependency_paths"
    )
    def test_config_snap_environment_with_dependencies_but_no_paths(
        self, mock_get_dependencies
    ):
        library_paths = {
            os.path.join(self.prime_dir, "lib1"),
            os.path.join(self.prime_dir, "lib2"),
        }
        mock_get_dependencies.return_value = library_paths
        project_config = self.make_snapcraft_project(self.snapcraft_yaml)

        # Ensure that LD_LIBRARY_PATH is present, but is completey empty since
        # no library paths actually exist.
        for variable in project_config.snap_env():
            self.assertFalse(
                "LD_LIBRARY_PATH" in variable,
                "Expected no LD_LIBRARY_PATH (got {!r})".format(variable),
            )

    def test_config_runtime_environment_ld(self):
        # Place a few ld.so.conf files in supported locations. We expect the
        # contents of these to make it into the LD_LIBRARY_PATH.
        mesa_dir = os.path.join(self.prime_dir, "usr", "lib", "my_arch", "mesa")
        os.makedirs(mesa_dir)
        with open(os.path.join(mesa_dir, "ld.so.conf"), "w") as f:
            f.write("/mesa")

        mesa_egl_dir = os.path.join(self.prime_dir, "usr", "lib", "my_arch", "mesa-egl")
        os.makedirs(mesa_egl_dir)
        with open(os.path.join(mesa_egl_dir, "ld.so.conf"), "w") as f:
            f.write("# Standalone comment\n")
            f.write("/mesa-egl")

        project_config = self.make_snapcraft_project(self.snapcraft_yaml)
        environment = project_config.snap_env()

        # Ensure that the LD_LIBRARY_PATH includes all the above paths
        paths = []
        for variable in environment:
            if "LD_LIBRARY_PATH" in variable:
                these_paths = variable.split("=")[1].strip()
                paths.extend(these_paths.replace('"', "").split(":"))

        self.assertTrue(len(paths) > 0, "Expected LD_LIBRARY_PATH to be in environment")

        expected = (os.path.join(self.prime_dir, i) for i in ["mesa", "mesa-egl"])
        for item in expected:
            self.assertTrue(
                item in paths, 'Expected LD_LIBRARY_PATH to include "{}"'.format(item)
            )

    def test_config_env_dedup(self):
        """Regression test for LP: #1767625.
        Verify that the use of after with multiple parts does not produce
        duplicate exports.
        """
        snapcraft_yaml = dedent(
            """\
            name: test
            base: core18
            version: "1"
            summary: test
            description: test
            confinement: strict
            grade: stable

            parts:
              main:
                plugin: nil
                after: [part1, part2, part3]
              part1:
                plugin: nil
              part2:
                plugin: nil
              part3:
                plugin: nil
        """
        )
        project_config = self.make_snapcraft_project(snapcraft_yaml)
        part = project_config.parts.get_part("main")
        environment = project_config.parts.build_env_for_part(part, root_part=True)
        # We sort here for equality checking but they should not be sorted
        # for a real case scenario.
        environment.sort()
        self.assertThat(
            environment,
            Equals(
                [
                    (
                        'PATH="{0}/parts/main/install/usr/sbin:'
                        "{0}/parts/main/install/usr/bin:"
                        "{0}/parts/main/install/sbin:"
                        '{0}/parts/main/install/bin${{PATH:+:$PATH}}"'
                    ).format(self.path),
                    (
                        'PATH="{0}/stage/usr/sbin:'
                        "{0}/stage/usr/bin:"
                        "{0}/stage/sbin:"
                        '{0}/stage/bin${{PATH:+:$PATH}}"'
                    ).format(self.path),
                    'PERL5LIB="{0}/stage/usr/share/perl5/"'.format(self.path),
                    'SNAPCRAFT_ARCH_TRIPLET="{}"'.format(
                        project_config.project.arch_triplet
                    ),
                    'SNAPCRAFT_CONTENT_DIRS=""',
                    'SNAPCRAFT_EXTENSIONS_DIR="{}"'.format(common.get_extensionsdir()),
                    'SNAPCRAFT_PARALLEL_BUILD_COUNT="2"',
                    'SNAPCRAFT_PART_BUILD="{}/parts/main/build"'.format(self.path),
                    'SNAPCRAFT_PART_BUILD_WORK="{}/parts/main/build/"'.format(
                        self.path
                    ),
                    'SNAPCRAFT_PART_INSTALL="{}/parts/main/install"'.format(self.path),
                    'SNAPCRAFT_PART_SRC="{}/parts/main/src"'.format(self.path),
                    'SNAPCRAFT_PART_SRC_WORK="{}/parts/main/src/"'.format(self.path),
                    'SNAPCRAFT_PRIME="{}/prime"'.format(self.path),
                    'SNAPCRAFT_PROJECT_DIR="{}"'.format(self.path),
                    'SNAPCRAFT_PROJECT_GRADE="stable"',
                    'SNAPCRAFT_PROJECT_NAME="test"',
                    'SNAPCRAFT_PROJECT_VERSION="1"',
                    'SNAPCRAFT_STAGE="{}/stage"'.format(self.path),
                    'SNAPCRAFT_TARGET_ARCH="{}"'.format(
                        project_config.project.target_arch
                    ),
                ]
            ),
        )

    def test_config_stage_environment_confinement_classic(self):
        self.useFixture(FakeOsRelease())

        snapcraft_yaml = dedent(
            """\
            name: test
            base: core18
            version: "1"
            summary: test
            description: test
            confinement: classic
            grade: stable

            parts:
              part1:
                plugin: nil
        """
        )
        project_config = self.make_snapcraft_project(snapcraft_yaml)
        part = project_config.parts.get_part("part1")
        environment = project_config.parts.build_env_for_part(part, root_part=True)
        self.assertThat(
            environment,
            Not(
                Contains(
                    'LD_LIBRARY_PATH="$LD_LIBRARY_PATH:{base_core_path}/lib:'
                    "{base_core_path}/usr/lib:{base_core_path}/lib/{arch_triplet}:"
                    '{base_core_path}/usr/lib/{arch_triplet}"'.format(
                        base_core_path=self.base_environment.core_path,
                        arch_triplet=project_config.project.arch_triplet,
                    )
                )
            ),
        )

    def test_parts_build_env_ordering_with_deps(self):
        snapcraft_yaml = dedent(
            """\
            name: test
            base: core18
            version: "1"
            summary: test
            description: test
            confinement: strict
            grade: stable

            parts:
              part1:
                plugin: nil
              part2:
                plugin: nil
                after: [part1]
        """
        )

        self.useFixture(fixtures.EnvironmentVariable("PATH", "/bin"))

        arch_triplet = snapcraft.ProjectOptions().arch_triplet
        self.maxDiff = None
        paths = [
            os.path.join(self.stage_dir, "lib"),
            os.path.join(self.stage_dir, "lib", arch_triplet),
            os.path.join(self.stage_dir, "usr", "lib"),
            os.path.join(self.stage_dir, "usr", "lib", arch_triplet),
            os.path.join(self.stage_dir, "include"),
            os.path.join(self.stage_dir, "usr", "include"),
            os.path.join(self.stage_dir, "include", arch_triplet),
            os.path.join(self.stage_dir, "usr", "include", arch_triplet),
            os.path.join(self.parts_dir, "part1", "install", "include"),
            os.path.join(self.parts_dir, "part1", "install", "lib"),
            os.path.join(self.parts_dir, "part2", "install", "include"),
            os.path.join(self.parts_dir, "part2", "install", "lib"),
        ]
        for path in paths:
            os.makedirs(path)

        project_config = self.make_snapcraft_project(snapcraft_yaml)
        part2 = [
            part for part in project_config.parts.all_parts if part.name == "part2"
        ][0]
        env = project_config.parts.build_env_for_part(part2)
        env_lines = "\n".join(["export {}\n".format(e) for e in env])

        shell_env = {
            "CFLAGS": "-I/user-provided",
            "CXXFLAGS": "-I/user-provided",
            "CPPFLAGS": "-I/user-provided",
            "LDFLAGS": "-L/user-provided",
            "LD_LIBRARY_PATH": "/user-provided",
        }

        def get_envvar(envvar):
            with tempfile.NamedTemporaryFile(mode="w+") as f:
                f.write(env_lines)
                f.write("echo ${}".format(envvar))
                f.flush()
                output = subprocess.check_output(["/bin/sh", f.name], env=shell_env)
            return output.decode(sys.getfilesystemencoding()).strip()

        expected_cflags = (
            "-I/user-provided "
            "-isystem{parts_dir}/part2/install/include -isystem{stage_dir}/include "
            "-isystem{stage_dir}/usr/include "
            "-isystem{stage_dir}/include/{arch_triplet} "
            "-isystem{stage_dir}/usr/include/{arch_triplet}".format(
                parts_dir=self.parts_dir,
                stage_dir=self.stage_dir,
                arch_triplet=project_config.project.arch_triplet,
            )
        )
        self.assertThat(get_envvar("CFLAGS"), Equals(expected_cflags))
        self.assertThat(get_envvar("CXXFLAGS"), Equals(expected_cflags))
        self.assertThat(get_envvar("CPPFLAGS"), Equals(expected_cflags))

        self.assertThat(
            get_envvar("LDFLAGS"),
            Equals(
                "-L/user-provided "
                "-L{parts_dir}/part2/install/lib -L{stage_dir}/lib "
                "-L{stage_dir}/usr/lib -L{stage_dir}/lib/{arch_triplet} "
                "-L{stage_dir}/usr/lib/{arch_triplet}".format(
                    parts_dir=self.parts_dir,
                    stage_dir=self.stage_dir,
                    arch_triplet=project_config.project.arch_triplet,
                )
            ),
        )

        self.assertThat(
            get_envvar("LD_LIBRARY_PATH"),
            Equals(
                "/user-provided:"
                "{parts_dir}/part2/install/lib:"
                "{stage_dir}/lib:"
                "{stage_dir}/usr/lib:"
                "{stage_dir}/lib/{arch_triplet}:"
                "{stage_dir}/usr/lib/{arch_triplet}".format(
                    parts_dir=self.parts_dir,
                    stage_dir=self.stage_dir,
                    arch_triplet=project_config.project.arch_triplet,
                )
            ),
        )

    @mock.patch("os.sched_getaffinity", return_value=set(range(0, 42)))
    def test_parts_build_env_contains_parallel_build_count(self, cpu_mock):
        project_config = self.make_snapcraft_project(self.snapcraft_yaml)
        part1 = [
            part for part in project_config.parts.all_parts if part.name == "part1"
        ][0]
        env = project_config.parts.build_env_for_part(part1)
        self.assertThat(env, Contains('SNAPCRAFT_PARALLEL_BUILD_COUNT="42"'))

    @mock.patch("os.sched_getaffinity", side_effect=AttributeError)
    @mock.patch("multiprocessing.cpu_count", return_value=42)
    def test_parts_build_env_contains_parallel_build_count_no_getaffinity(
        self, affinity_mock, cpu_mock
    ):
        project_config = self.make_snapcraft_project(self.snapcraft_yaml)
        part1 = [
            part for part in project_config.parts.all_parts if part.name == "part1"
        ][0]
        env = project_config.parts.build_env_for_part(part1)
        self.assertThat(env, Contains('SNAPCRAFT_PARALLEL_BUILD_COUNT="42"'))

    @mock.patch("os.sched_getaffinity", side_effect=AttributeError)
    @mock.patch("multiprocessing.cpu_count", side_effect=NotImplementedError)
    def test_parts_build_env_contains_parallel_build_count_no_cpucount(
        self, affinity_mock, cpu_mock
    ):
        project_config = self.make_snapcraft_project(self.snapcraft_yaml)
        part1 = [
            part for part in project_config.parts.all_parts if part.name == "part1"
        ][0]
        env = project_config.parts.build_env_for_part(part1)
        self.assertThat(env, Contains('SNAPCRAFT_PARALLEL_BUILD_COUNT="1"'))

    def test_extension_dir(self):
        common.set_extensionsdir("/foo")
        project_config = self.make_snapcraft_project(self.snapcraft_yaml)
        part1 = [
            part for part in project_config.parts.all_parts if part.name == "part1"
        ][0]
        env = project_config.parts.build_env_for_part(part1)
        self.assertThat(env, Contains('SNAPCRAFT_EXTENSIONS_DIR="/foo"'))

    def test_project_dir(self):
        project_config = self.make_snapcraft_project(self.snapcraft_yaml)
        env = project_config.parts.build_env_for_part(project_config.parts.all_parts[0])
        self.assertThat(env, Contains('SNAPCRAFT_PROJECT_DIR="{}"'.format(self.path)))

    def test_content_dirs_default(self):
        project_config = self.make_snapcraft_project(self.snapcraft_yaml)
        env = project_config.parts.build_env_for_part(project_config.parts.all_parts[0])
        self.assertThat(env, Contains('SNAPCRAFT_CONTENT_DIRS=""'))

    @mock.patch(
        "snapcraft.project._project.Project._get_provider_content_dirs",
        return_value=sorted({"/tmp/test1", "/tmp/test2"}),
    )
    def test_content_dirs(self, mock_get_content_dirs):
        project_config = self.make_snapcraft_project(self.snapcraft_yaml)
        env = project_config.parts.build_env_for_part(project_config.parts.all_parts[0])
        self.assertThat(env, Contains('SNAPCRAFT_CONTENT_DIRS="/tmp/test1:/tmp/test2"'))

    def test_build_environment(self):
        self.useFixture(FakeOsRelease())

        snapcraft_yaml = dedent(
            """\
            name: test
            base: core18
            version: "1"
            summary: test
            description: test
            confinement: strict
            grade: stable

            parts:
              part1:
                plugin: nil
                build-environment:
                  - FOO: BAR
        """
        )
        project_config = self.make_snapcraft_project(snapcraft_yaml)
        part = project_config.parts.get_part("part1")
        environment = project_config.parts.build_env_for_part(part)
        self.assertThat(environment, Contains('FOO="BAR"'))

    def test_build_environment_can_depend_on_global_env(self):
        self.useFixture(FakeOsRelease())

        snapcraft_yaml = dedent(
            """\
            name: test
            base: core18
            version: "1"
            summary: test
            description: test
            confinement: strict
            grade: stable

            parts:
              part1:
                plugin: nil
                build-environment:
                  - PROJECT_NAME: $SNAPCRAFT_PROJECT_NAME
        """
        )
        project_config = self.make_snapcraft_project(snapcraft_yaml)
        part = project_config.parts.get_part("part1")
        environment = project_config.parts.build_env_for_part(part)
        snapcraft_definition_index = -1
        build_environment_definition_index = -1
        for index, variable in enumerate(environment):
            if variable.startswith("SNAPCRAFT_PROJECT_NAME="):
                snapcraft_definition_index = index
            if variable.startswith("PROJECT_NAME="):
                build_environment_definition_index = index

        # Assert that each definition was found, and the global env came before the
        # build environment
        self.assertThat(snapcraft_definition_index, GreaterThan(-1))
        self.assertThat(build_environment_definition_index, GreaterThan(-1))
        self.assertThat(
            build_environment_definition_index, GreaterThan(snapcraft_definition_index)
        )

    def test_build_environment_can_depend_on_part_env(self):
        self.useFixture(FakeOsRelease())

        snapcraft_yaml = dedent(
            """\
            name: test
            base: core18
            version: "1"
            summary: test
            description: test
            confinement: strict
            grade: stable

            parts:
              part1:
                plugin: nil
                build-environment:
                  - PART_INSTALL: $SNAPCRAFT_PART_INSTALL
        """
        )
        project_config = self.make_snapcraft_project(snapcraft_yaml)
        part = project_config.parts.get_part("part1")
        environment = project_config.parts.build_env_for_part(part)
        snapcraft_definition_index = -1
        build_environment_definition_index = -1
        for index, variable in enumerate(environment):
            if variable.startswith("SNAPCRAFT_PART_INSTALL="):
                snapcraft_definition_index = index
            if variable.startswith("PART_INSTALL="):
                build_environment_definition_index = index

        # Assert that each definition was found, and the part env came before the
        # build environment
        self.assertThat(snapcraft_definition_index, GreaterThan(-1))
        self.assertThat(build_environment_definition_index, GreaterThan(-1))
        self.assertThat(
            build_environment_definition_index, GreaterThan(snapcraft_definition_index)
        )

    def test_build_environment_with_dependencies_does_not_leak(self):
        self.useFixture(FakeOsRelease())

        snapcraft_yaml = dedent(
            """\
            name: test
            base: core18
            version: "1"
            summary: test
            description: test
            confinement: strict
            grade: stable

            parts:
              part1:
                plugin: nil
                build-environment:
                  - FOO: BAR

              part2:
                plugin: nil
                after: [part1]
                build-environment:
                  - BAZ: QUX
        """
        )
        project_config = self.make_snapcraft_project(snapcraft_yaml)
        part1 = project_config.parts.get_part("part1")
        part2 = project_config.parts.get_part("part2")
        self.assertThat(
            project_config.parts.build_env_for_part(part1), Contains('FOO="BAR"')
        )
        self.assertThat(
            project_config.parts.build_env_for_part(part2), Not(Contains('FOO="BAR"'))
        )
        self.assertThat(
            project_config.parts.build_env_for_part(part2), Contains('BAZ="QUX"')
        )
