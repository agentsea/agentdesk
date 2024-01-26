#!/bin/bash

yq '.availabilityZones[]' cluster.yaml -r | \
    xargs -I{} bash -c "
        export EKS_AZ={};
        envsubst < node-group.yaml.template | \
        eksctl create nodegroup --config-file -
    "
