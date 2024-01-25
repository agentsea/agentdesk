#!/bin/bash

yq '.availabilityZones[]' cluster.yaml -r | \                                                                                        ok  18s  3.10.1 py  15:54:14
    xargs -I{} bash -c "
        export EKS_AZ={};
        envsubst < node-group.yaml.template | \
        eksctl create nodegroup --config-file -
    "
