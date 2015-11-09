from buildbot.plugins import *
from buildbot.schedulers.forcesched import *

import os

class InheritProperties(ChoiceStringParameter):

    """A parameter that takes its values from another build"""
    type = ChoiceStringParameter.type + ["inherit"]
    name = "inherit"
    compatible_builds = None
    copy_properties = []

    def getChoices(self, master, scheduler, buildername):
        return self.compatible_builds(master.status, buildername)

    def getFromKwargs(self, kwargs):
        raise ValidationError("InheritBuildParameter can only be used by properties")

    def updateFromKwargs(self, master, properties, changes, kwargs, **unused):
        arg = kwargs.get(self.fullName, [""])[0]
        splitted_arg = arg.split(" ")[0].split("/")
        if len(splitted_arg) != 2:
            raise ValidationError("bad build: %s" % (arg))
        builder, num = splitted_arg
        builder_status = master.status.getBuilder(builder)
        if not builder_status:
            raise ValidationError("unknown builder: %s in %s" % (builder, arg))
        b = builder_status.getBuild(int(num))
        if not b:
            raise ValidationError("unknown build: %d in %s" % (num, arg))
        props = {self.name: (arg.split(" ")[0])}
        for name in self.copy_properties:
            props[name] = b.getProperty(name)
        properties.update(props)
        changes.extend(b.changes)

def get_shinken_successful_builds(status, builder):
    if builder == None: # this is the case for force_build_all
        return ["cannot generate build list here"]
    # find all successful builds2
    target_builder = "build-shinken"
    builds = []
    builder_status = status.getBuilder(target_builder)
    for num in xrange(1,40): # 40 last builds
        b = builder_status.getBuild(-num)
        if not b:
            continue
        if b.getResults() == util.FAILURE:
            continue
        builds.append(target_builder+"/"+str(b.getNumber()))
    return builds

change_source_list = []
change_source_list.append(changes.GitPoller(
        'https://aur.archlinux.org/shinken.git',
        workdir='gitpoller-shinken', branch='master',
        pollinterval=300))

schedulers_list = []
schedulers_list.append(schedulers.SingleBranchScheduler(
                            name="build-shinken",
                            change_filter=util.ChangeFilter(branch='master'),
                            treeStableTimer=None,
                            builderNames=["build-shinken"]))
schedulers_list.append(schedulers.ForceScheduler(
                            name="force-build-shinken",
                            builderNames=["build-shinken"]))
schedulers_list.append(schedulers.ForceScheduler(
                            name="promote-shinken-build",
                            builderNames=["promote-shinken"],
                            properties=[
                                InheritProperties(
                                    name="inherit",
                                    label="promote a build on repos",
                                    compatible_builds=get_shinken_successful_builds,
                                    copy_properties=[
                                        'master_store_build',
                                        'packages'],
                                    required = True),
                                ]))
builders_list = []

fbuildshinken = util.BuildFactory()
# check out the source
fbuildshinken.addStep(steps.Git(
    repourl='https://aur.archlinux.org/shinken.git',
    mode='incremental'))

#### Build shinken package ###

# Install buildeps
fbuildshinken.addStep(steps.ShellCommand(
    command=['/bin/sh',
             '-c',
             '. ./PKGBUILD; echo "${makedepends[@]}"'
             ' | xargs pacman -Sy --asdeps --needed --noconfirm']))
# Install build dependencies (not explicitly installed), don't reinstall,
# install new build without confirmation

fbuildshinken.addStep(steps.RemoveDirectory(dir="build/target"))
fbuildshinken.addStep(steps.MakeDirectory(dir="build/target"))

# To allow 'nobody' build package
fbuildshinken.addStep(steps.ShellCommand(
    command=['chmod', '777', '.', 'target']))

fbuildshinken.addStep(steps.ShellCommand(
    command=['sudo', '-u', 'nobody', 'makepkg', '-Cf', 'PKGDEST=target']))

def glob2package_list(rc, stdout, stderr):
    pkgs = []
    for lines in stdout.splitlines():
        file_ = os.path.basename(lines.strip())
        if file_:
            pkgs.append(file_)
    return {'packages': pkgs}

fbuildshinken.addStep(steps.SetPropertyFromCommand(
    command='ls -1 target/*.pkg.tar.xz',
    extract_fn=glob2package_list))

fbuildshinken.addStep(steps.SetProperty(
    property='master_store_build',
    value=util.Interpolate('pkgs/%(prop:buildername)s/%(prop:buildnumber)s/')))

fbuildshinken.addStep(steps.DirectoryUpload(
    slavesrc='target',
    masterdest=util.Interpolate('%(prop:master_store_build)s')))

builders_list.append(
    util.BuilderConfig(name="build-shinken",
      slavenames=["arch_slave"],
      factory=fbuildshinken))

# Promote on repo

fpromoteonrepo = util.BuildFactory()

def property_item(property_name, item):
    @util.renderer
    def render(props):
        return props.getProperty(property_name)[item]
    return render

def has_property(*args):
    @util.renderer
    def render(props):
        return all([props.hasProperty(p) for p in args])
    return render

fpromoteonrepo.addStep(steps.FileDownload(
    mastersrc=util.Interpolate('%(prop:master_store_build)s/%(kw:pkg)s',
                                pkg=property_item('packages', 0)),
    slavedest=util.Interpolate('%(kw:pkg)s',
                                pkg=property_item('packages', 0))))

fpromoteonrepo.addStep(steps.SetPropertiesFromEnv(variables=["REPO_PATH", "REPO_NAME"]))
fpromoteonrepo.addStep(steps.SetProperty(
    property='db_path',
    value=util.Interpolate('%(prop:REPO_PATH:~%(prop:workdir)s)s/'
                           '%(prop:REPO_NAME:~default_repo)s.db.tar.xz')))
fpromoteonrepo.addStep(steps.SetProperty(
    property='pkg_path',
    value=util.Interpolate('%(prop:REPO_PATH:~%(prop:workdir)s)s/'
                           '%(kw:pkg)s', pkg=property_item('packages', 0)
    )))
fpromoteonrepo.addStep(steps.ShellCommand(
    command=['mv',
             property_item('packages', 0),
             util.Interpolate('%(prop:pkg_path)s')]))

fpromoteonrepo.addStep(steps.ShellCommand(
    command=['repo-add',
             util.Interpolate('%(prop:db_path)s'),
             util.Interpolate('%(prop:pkg_path)s')],
    doStepIf=lambda step: has_property('REPO_PATH', 'REPO_NAME')
    ))

builders_list.append(
    util.BuilderConfig(name="promote-shinken",
      slavenames=["repo_slave"],
      factory=fpromoteonrepo))
