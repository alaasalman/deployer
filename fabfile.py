#!/usr/bin/env python
import json

from fabric.api import *
from fabric.colors import yellow, red, green
from fabric.contrib.files import *


def _install_package(pkg_name):
    with settings(hide('warnings', 'stderr'), warn_only=True):
        result = sudo('dpkg-query --show {0}'.format(pkg_name))

    if result.failed is False:
        warn('{0} is already installed'.format(pkg_name))
    else:
        sudo('apt -y install {0}'.format(pkg_name))


def loadconfig():
    """
    Load serverconf.json configuration file and replace environment values with ones defined in it.
    """
    config_file_path = 'conf.json'

    if not os.path.exists(config_file_path):
        red('Couldn\'t find config file at {0}'.format(config_file_path))
        return
    
    with open(config_file_path) as conf_file:
        json_config_object = json.load(conf_file)

        print(green('Using this configuration:'))
        for k, v in json_config_object.items():
            env[k] = v
            print(green('{0}:{1}'.format(k, v)))


def _addsshkey(username):
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


def addadminuser():
    """
        Add an all-powerful admin user as a sudo'er. User is added with a disabled password to be given
        SSH key only access later. The user is also added as a group of an admin group. If admin group
        doesn't exist, it is created.
    """
    admin_user = env['admin_user']
    user_home = '/home/{username}'.format(username=admin_user)
    admin_group = env['admin_group']

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

    _addsshkey(admin_user)


def securessh():
    """
    Stop root from ssh'ing in and disallow password authentication. This should be done as last step.
    """
    sed('/etc/ssh/sshd_config', 'PermitRootLogin yes', 'PermitRootLogin no', use_sudo=True)
    sed('/etc/ssh/sshd_config', '#PasswordAuthentication yes', 'PasswordAuthentication no', use_sudo=True)
    sudo('service ssh restart')


def setupfirewall():
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
    append(iptables_init_file, iptables_init_text, use_sudo=True)
    sudo('chmod +x %s' % iptables_init_file)
    sudo(iptables_init_file)


def installpackages():
    pkg_list = [
        # editors and extra
        'emacs24-nox',
        'git',

        # process supervisor
        'supervisor',

        # python dev package
        'python-dev',
        'virtualenv',
        'virtualenvwrapper',

        # install logwatch
    #    'logwatch',

        # install fail2ban
        'fail2ban',

        # install nginx
        'nginx',

        # install postgresql
        'postgresql',
        'postgresql-contrib',
        'postgresql-server-dev-9.5'
    ]

    # update package list before starting
    sudo('apt update')

    for pkg in pkg_list:
        _install_package(pkg)


def setupnewserver():
    # don't forget to call loadconfig() first
    addadminuser()
    securessh()
    setupfirewall()
    installpackages()
