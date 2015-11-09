
To use this repository:
```
pacman -S ansible
ansible-galaxy install -r requirements.yml -p roles
ansible-playbook -i inventory.yml buildbot_pkg.yml
```
