# Kubevirt Deployment (experimental)

This follows https://kubevirt.io/2023/KubeVirt-on-autoscaling-nodes.html with some modifications

## Prerequisite

- direnv
- yq
- eksctl

## Usage

Set your values in `.envrc`, then to create a cluster run `./create-cluster.sh`

Once finished you can create the VM node groups with `./create-ng.sh`

Then, install the Kubevirt operator with `./install-kubevirt.sh`

Next, deploy Kubevirt with `./deploy-kubevirt.sh`

## Debug

Sign in to your AWS console and check cloudformation for more information on failures.
