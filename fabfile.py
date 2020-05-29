#!/usr/bin/env python
import json
from os import path

from fabric.api import (sudo, settings, require,
                        cd, env, task,
                        local, run)
from fabric.colors import yellow, red, green
from fabric.context_managers import shell_env, prefix, hide
from fabric.contrib.files import append, sed, put, exists
from fabric.utils import abort, warn

# use ssh config attribs in $HOME
env.use_ssh_config = True
# make sure bash session is interactive to load aliases
env.shell = '/bin/bash -l -i -c'
# load config only once
env.config_loaded = False


def print_with_attention(msg):
    """
    Print supplied message within ascii marks to bring the user's attention to it.
    :param msg: str to print and alert user of
    """
    print(yellow("=" * len(msg)))
    print(yellow(msg))
    print(yellow("=" * len(msg)))


def install_package(pkg_name):
    sudo('apt --yes install {0}'.format(pkg_name), quiet=True)


def loadconfig():
    """
    Load conf.json configuration file and replace environment values with ones defined in it.
    """
    if env.config_loaded:
        return

    print_with_attention("Loading configuration")

    require('config_key')  # need to know what environment we're working on

    config_file_path = 'conf.json'

    if not path.exists(config_file_path):
        abort("Couldn\'t find config file at {0}".format(config_file_path))

    with open(config_file_path) as conf_file:
        json_config_object = json.load(conf_file)
        env_config_object = json_config_object.get(env.config_key)

        if env_config_object is None:
            abort("You need to specify what environment I am targeting first")

        print(green("Using this configuration:"))
        for k, v in env_config_object.items():
            env[k] = v
            print(green("{0} => {1}".format(k, v)))

    env.config_loaded = True


def addsshkey(username):
    """
        Copy ssh key to the remote user's home directory. This also sets up the ssh dir in remote ${HOME}
        and sets up proper permissions.
    """
    sshdir = '/home/{username}/.ssh'.format(username=username)
    local_pub_key_filename = '/home/{username}/.ssh/id_rsa.pub'.format(username=username)

    if not exists(sshdir):
        sudo('mkdir %s' % sshdir)
    
        local_pub_key = open(local_pub_key_filename).read()

        with cd(sshdir):
            append('authorized_keys', local_pub_key, use_sudo=True)

        sudo('chown -R {username}:{username} {sshdir}'.format(username=username, sshdir=sshdir))
        sudo('chmod go-rwx -R %s' % sshdir)
    else:
        print(yellow("{username} ssh dir exists, not doing anything".format(username=username)))


@task
def addadminuser():
    """
        Add an all-powerful admin user as a sudo'er
        User is added with a disabled password to be given SSH key only access later. The user is also
        added as a group of an admin group. If admin group doesn't exist, it is created.
    """

    loadconfig()

    require('admin_user', 'admin_group')

    admin_user = env.admin_user

    user_home = '/home/{username}'.format(username=admin_user)
    admin_group = env.admin_group

    if not exists(user_home):
        sudo('adduser {username} --disabled-password --gecos ""'.format(username=admin_user))

        sudo('adduser {username} {group}'.format(
            username=admin_user,
            group=admin_group)
        )

        # admin user needs no password to sudo
        append('/etc/sudoers.d/{username}', '{username} ALL=(ALL) NOPASSWD:ALL'.format(username=admin_user),
               use_sudo=True)
    else:
        print(yellow("{username} user already exists".format(username=admin_user)))

    addsshkey(admin_user)


@task
def securessh():
    """
    Stop root from ssh'ing in and disallow password authentication. This should be done as last step
    """
    loadconfig()

    sed('/etc/ssh/sshd_config', 'PermitRootLogin yes', 'PermitRootLogin no', use_sudo=True)
    sed('/etc/ssh/sshd_config', '#PasswordAuthentication yes', 'PasswordAuthentication no', use_sudo=True)
    sudo('service ssh restart')


@task
def setupfirewall():
    """
    Set up a somewhat strict deny-all iptables-based firewall
    Only Allow 80,443, 22 and ICMP in otherwise deny. Also records all denied requests to monitor for abuse.
    """
    loadconfig()

    iptables_rules_file = 'iptables.firewall.rules'
    iptables_init_file = '/etc/network/if-pre-up.d/firewall'

    if exists(iptables_init_file):
        print(yellow('firewall file already exists, doing nothing'))
        return
    
    put(iptables_rules_file, '/etc/', use_sudo=True)

    iptables_init_text = """
    #!/bin/sh
    /sbin/iptables-restore < /etc/iptables.firewall.rules
    """
    append(iptables_init_file, iptables_init_text.strip(), use_sudo=True)
    sudo('chmod +x %s' % iptables_init_file)
    sudo(iptables_init_file)


@task
def installpackages():
    """
    Install needed system packages
    """
    loadconfig()

    pkg_list = [
        # editors and extra
        'emacs-nox',
        'git',

        # process supervisor
        'supervisor',

        # python dev package
        'build-essential',
        'python3-dev',
        'virtualenv',

        # install fail2ban
        'fail2ban',

        # install nginx
        'nginx',

        # install postgresql
        'postgresql',
        'postgresql-contrib',
        'postgresql-server-dev-all',

        # some sweet CLI
        'zsh',
        'tmux',
    ]

    print_with_attention("Updating and installing packages")

    # update package list before starting
    print(green("Updating repos"))
    sudo('apt update', quiet=True)

    for index, pkg_name in enumerate(pkg_list, start=1):
        print("Installing {0} {1}/{2}".format(pkg_name, index, len(pkg_list)))
        install_package(pkg_name)


@task
def setupdjangoapp():
    """
    Set up a new django app instance
    """
    loadconfig()

    require('app_name')

    app_home = '/home/{0}'.format(env.app_name)

    # create the user
    if not exists(app_home):
        print(green('Adding application user {0}'.format(env.app_name)))
        sudo('adduser {username} --disabled-password --disabled-login --gecos ""'.format(username=env.app_name))

        with settings(sudo_user=env.app_name, quiet=True):
            # create an ssh key for the new user
            sudo('ssh-keygen -f "{0}/.ssh/id_rsa" -t rsa -N ""'.format(app_home))
            print_with_attention("Add this app key as your deploy key")
            sudo('cat {0}/.ssh/id_rsa.pub'.format(app_home))

            # setup virtualenv and needed folders
            with settings(cd(app_home), shell_env(HOME=app_home)):
                print(green("Getting app user home ready"))
                # create app dir
                sudo('mkdir {0}'.format(env.app_name))
                # for logs, obviously
                sudo('mkdir logs')
                # django's static files
                sudo('mkdir static')
                # uploaded media files, if any
                sudo('mkdir media')
                # download pip installer as per recommendation - admin to install manually
                sudo('curl https://bootstrap.pypa.io/get-pip.py -o get-pip.py')
                print("get-pip.py script ready in $HOME")
                # download poetry installer - admin to install manually
                sudo('curl -sSL https://raw.githubusercontent.com/python-poetry/poetry/master/get-poetry.py -o get-poetry.py')
                print("get-poetry.py script ready in $HOME")


    # then create app database and app database user
    sudo('createuser %(app_name)s --pwprompt' % env, user='postgres')
    sudo('createdb %(app_name)s --owner=%(app_name)s' % env, user='postgres')


@task
def setup():
    """
    Set up a new machine from a clean slate
    """
    loadconfig()

    addadminuser()
    securessh()
    setupfirewall()
    installpackages()


@task
def defaultserver():
    env.config_key = 'default'

    loadconfig()
