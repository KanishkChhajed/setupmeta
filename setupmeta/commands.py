"""
Commands contributed by setupmeta
"""

import collections
import os
import shutil
from distutils.command.check import check as check_cmd
from itertools import chain

import setuptools

import setupmeta


flatten = chain.from_iterable


def MetaCommand(cls):
    """Decorator allowing for less boilerplate in our commands"""
    return setupmeta.MetaDefs.register_command(cls)


def count(*args):
    return sum(1 for a in args if a)


def longest_line(lines, maximum=70):
    lines = [setupmeta.stringify(line) for line in lines]
    longest = max(len(line) for line in lines if "\n" not in line)
    return min(longest, maximum)


@MetaCommand
class CheckCommand(check_cmd):
    """Perform checks on the package"""

    user_options = check_cmd.user_options + [
        ("status", "t", "Show git status recap (useful to get evidence as to why version was dirty during CI jobs)"),
        ("reqs", "q", "Show how many requirements were auto-abstracted or ignored, if any"),
    ]

    def initialize_options(self):
        check_cmd.initialize_options(self)
        self.status = None
        self.reqs = None

    def run(self):
        if not self.setupmeta:
            return check_cmd.run(self)

        if count(self.restructuredtext, self.status, self.reqs) == 0:
            self.status = 1
            self.reqs = 1

        if self.reqs:
            self._show_requirements_synopsis()

        if self.status:
            self._show_git_status()

        check_cmd.run(self)

    def _show_requirements_synopsis(self):
        """Show how many requirements were auto-abstracted or ignored, if any"""
        reqs = self.setupmeta.requirements and self.setupmeta.requirements.install_requires
        if reqs and reqs.filled_requirements and (reqs.abstracted or reqs.ignored):
            message = "[setupmeta] install_requires: %s abstracted, %s ignored, %s untouched" % (
                len(reqs.abstracted),
                len(reqs.ignored),
                len(reqs.untouched),
            )
            print(message)

    def _show_git_status(self):
        if self.setupmeta.versioning:
            scm = self.setupmeta.versioning.scm
            if scm:
                diff = scm.get_output("diff", "--stat", capture=True)
                if diff:
                    print("Pending changes:\n%s" % diff)


@MetaCommand
class VersionCommand(setuptools.Command):
    """show/bump version managed by setupmeta"""

    user_options = [
        ("bump=", "b", "bump specified part of version"),
        ("commit", "c", "commit bump"),
        ("push", None, "push version bump"),
        ("show-next=", "a", "show what the next bump of the specified part of version will be"),
        ("simulate-branch=", "s", "simulate branch name (useful for testing)"),
    ]

    def initialize_options(self):
        self.bump = None
        self.commit = 0
        self.push = 0
        self.simulate_branch = None
        self.show_next = None

    def run(self):
        if not self.setupmeta:
            return

        try:
            if self.show_next:
                print(self.setupmeta.versioning.get_bump(self.show_next))

            elif self.bump:
                self.setupmeta.versioning.bump(self.bump, commit=self.commit, push=self.push, simulate_branch=self.simulate_branch)

            else:
                print(self.setupmeta.version)

        except setupmeta.UsageError as e:
            from distutils.errors import DistutilsSetupError

            raise DistutilsSetupError(e)


@MetaCommand
class ExplainCommand(setuptools.Command):
    """Show a report of where key/values setup(attr) come from"""

    user_options = [
        ("dependencies", "d", "show auto-filled dependencies"),
        ("expand", "x", "show expanded setup.py, as it would be without setupmeta"),
        ("recommend", "r", "show more recommendations"),
        ("chars=", "c", "max chars to show"),
    ]

    def initialize_options(self):
        self.dependencies = False
        self.expand = False
        self.recommend = False
        self.chars = setupmeta.Console.columns()

    def check_recommend(self, key, hint=None):
        if key not in self.setupmeta.definitions:
            hint = ", %s" % hint if hint else ""
            self.setupmeta.auto_fill(key, "- Consider specifying '%s'%s" % (key, hint), "missing")

    def represented_req(self, name, source_description, align):
        name = '"%s",' % name
        if source_description:
            fmt = "%%-%ss# %%s" % align
            name = fmt % (name, source_description)

        return name

    def show_requirements(self, setup_key, requirements):
        """
        :param str setup_key: Name of corresponding key in 'setup()'
        :param setupmeta.RequirementsFile requirements:
        """
        content = "None,   # no auto-fill"
        names = []
        source_descriptions = []
        if requirements:
            for req_entry in requirements.reqs:
                if req_entry.requirement and not req_entry.is_ignored and req_entry.requirement not in names:
                    names.append(req_entry.requirement)
                    source_descriptions.append(req_entry.source_description)

        if names:
            longest_name = max(len(name) for name in names) + 5
            content = []
            for i, name in enumerate(names):
                content.append(self.represented_req(name, source_descriptions[i], longest_name))

            content = "[\n        %s\n    ]," % "\n        ".join(content).strip()

        print("    %s=%s" % (setup_key, content))

    def show_dependencies(self):
        """Copy-pastable code snippet with install_requires"""
        print("    # This reflects only auto-fill, doesn't look at explicit settings from your setup.py")
        install_requires = None
        if self.setupmeta.requirements:
            install_requires = self.setupmeta.requirements.install_requires

        self.show_requirements("install_requires", install_requires)

    def show_expanded_python(self):
        """Copy-pastable setup.py, if one wants to get rid of setupmeta"""
        definitions = self.setupmeta.definitions
        print('"""\nGenerated by https://pypi.org/project/setupmeta/\n"""\n')
        print("from setuptools import setup\n\n")

        version = definitions.get("version")
        if version:
            print("__version__ = %s\n\n" % setupmeta.stringify(version.value, quote=True))

        print("setup(")

        defs = []
        for definition in sorted(definitions.values()):
            if not definition.value or definition.key not in setupmeta.MetaDefs.all_fields:
                continue

            if definition.key == "setup_requires":
                # When expanding, remove mention of 'setupmeta',
                # as expansion is aimed at giving a people a way to get a setup.py as-if setupmeta didn't exist
                # ie: it's a way of easily getting rid of setupmeta (should the need arise)
                if "setupmeta" in definition.value:
                    definition.value.remove("setupmeta")

                if definition.value:
                    definition.value = setupmeta.stringify(definition.value, quote=True, indent="        ")

            elif definition.key == "download_url":
                if version and version.value in definition.value:
                    definition.value = definition.value.replace(version.value, "%s")
                    definition.value = "%s %% __version__" % setupmeta.stringify(setupmeta.short(definition.value), quote=True)

                else:
                    definition.value = setupmeta.stringify(definition.value, quote=True, indent="        ")

            elif definition.key == "long_description":
                definition.value = "open(%s).read()" % setupmeta.stringify(setupmeta.short(definition.source), quote=True)

            elif definition.key == "version":
                definition.value = "__version__"

            elif definition.key != "include_package_data":
                definition.value = setupmeta.stringify(definition.value, quote=True, indent="        ")

            if definition.value:
                defs.append(definition)

        longest = longest_line([d.value for d in defs])
        for definition in defs:
            if definition.key == "versioning":
                line = "    # versioning=%s," % definition.value

            else:
                line = "    %s=%s," % (definition.key, definition.value)

            source = definition.actual_source
            if source and source != "explicit":
                comment = "# from %s" % setupmeta.short(source)
                rest, _, last_line = line.rpartition("\n")
                if len(last_line) < longest:
                    padding = " " * (longest - len(last_line))

                else:
                    padding = " "

                last_line = "%s%s%s" % (last_line, padding, comment)
                line = "%s\n%s" % (rest, last_line) if rest else last_line

            print(line)

        print(")")

    def run(self):
        if not self.setupmeta:
            return

        if self.expand:
            return self.show_expanded_python()

        if self.dependencies:
            return self.show_dependencies()

        self.chars = setupmeta.to_int(self.chars, default=setupmeta.Console.columns())

        definitions = self.setupmeta.definitions
        self.check_recommend("name")
        self.check_recommend("version", "you can use setupmeta's versioning='...'")
        self.check_recommend("description", "add a README or a docstring to your module")
        self.check_recommend("long_description", "add a README file")
        if self.recommend:
            self.check_recommend("author")
            self.check_recommend("download_url")
            self.check_recommend("license")
            self.check_recommend("url")

        if definitions:
            longest_key = min(30, max(len(key) for key in definitions))
            sources = sum((d.sources for d in definitions.values()), [])
            longest_source = min(40, max(len(s.source) for s in sources))
            form = "%%%ss: (%%%ss) %%s" % (longest_key, -longest_source)
            max_chars = max(60, self.chars - longest_key - longest_source - 5)

            for definition in sorted(definitions.values()):
                count = 0
                for source in definition.sources:
                    if count:
                        prefix = "\\_"

                    elif source.key not in setupmeta.MetaDefs.all_fields:
                        prefix = "%s*" % source.key

                    else:
                        prefix = source.key

                    preview = setupmeta.short(source.value, c=max_chars)
                    s = form % (prefix, setupmeta.short(source.source), preview)
                    print(s)
                    count += 1


@MetaCommand
class EntryPointsCommand(setuptools.Command):
    """List entry points for pygradle consumption"""

    def run(self):
        if not self.setupmeta:
            return

        entry_points = self.setupmeta.value("entry_points")
        console_scripts = get_console_scripts(entry_points)
        if not console_scripts:
            return

        if isinstance(console_scripts, list):
            for ep in console_scripts:
                print(ep)

            return

        for line in console_scripts.splitlines():
            line = line.strip()
            if line:
                print(line)


def get_console_scripts(entry_points):
    """pygradle's 'entrypoints' are misnamed: they really mean 'consolescripts'"""
    if not entry_points:
        return None

    if isinstance(entry_points, dict):
        return entry_points.get("console_scripts")

    if isinstance(entry_points, list):
        result = []
        in_console_scripts = False
        for line in entry_points:
            line = line.strip()
            if line and line.startswith("["):
                in_console_scripts = "console_scripts" in line
                continue

            if in_console_scripts:
                result.append(line)

        return result

    return get_console_scripts(entry_points.split("\n"))


@MetaCommand
class CleanCommand(setuptools.Command):
    """Clean build artifacts and virtual envs"""

    direct = set(".cache .tox build dist venv".split())
    ignored = set(".git .gradle .idea .venv".split())
    dirs = set("__pycache__".split())
    extensions = set("egg-info pyc pyo pyd".split())

    deleted = 0
    by_ext = None

    def delete(self, full_path):
        if os.path.isdir(full_path):
            shutil.rmtree(full_path)
            print("deleted %s" % setupmeta.relative_path(full_path))

        else:
            os.unlink(full_path)
            self.by_ext[full_path.rpartition(".")[2]] += 1

        self.deleted += 1

    def clean_direct(self):
        for target in self.direct:
            full_path = setupmeta.project_path(target)
            if os.path.exists(full_path):
                self.delete(full_path)

    def run(self):
        if not self.setupmeta:
            return

        self.deleted = 0
        self.by_ext = collections.defaultdict(int)
        self.clean_direct()
        for dirpath, dirnames, filenames in os.walk(setupmeta.MetaDefs.project_dir):
            remove = []
            for dname in dirnames:
                if dname in self.ignored:
                    remove.append(dname)

                elif dname in self.dirs:
                    remove.append(dname)
                    self.delete(os.path.join(dirpath, dname))

                else:
                    ext = dname.rpartition(".")[2]
                    if ext in self.extensions:
                        remove.append(dname)
                        self.delete(os.path.join(dirpath, dname))

            for dname in remove:
                dirnames.remove(dname)

            for fname in filenames:
                ext = fname.rpartition(".")[2]
                if ext in self.extensions:
                    self.delete(os.path.join(dirpath, fname))

        if self.by_ext:
            info = ["%s .%s files" % (v, k) for k, v in sorted(self.by_ext.items())]
            print("deleted %s" % ", ".join(info))

        if self.deleted == 0:
            print("all clean, no deletable files found")
