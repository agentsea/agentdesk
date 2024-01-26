from google.cloud import compute_v1


def create_custom_image(project_id, image_name, bucket_name, image_file):
    """
    Create a custom image from a file in Cloud Storage.

    Args:
    project_id (str): The ID of the Google Cloud project.
    image_name (str): The name to assign to the new custom image.
    bucket_name (str): The name of the Google Cloud Storage bucket where the image file is stored.
    image_file (str): The name of the image file in the Google Cloud Storage bucket.

    Returns:
    The operation result of creating the image.
    """
    images_client = compute_v1.ImagesClient()
    image = compute_v1.Image()
    image.name = image_name
    image.source_image = f"gs://{bucket_name}/{image_file}"

    operation = images_client.insert(project=project_id, image_resource=image)
    return operation.result()


def create_vm_instance(project_id, zone, instance_name, machine_type, image_name):
    """
    Create a new VM instance with the specified custom image.

    Args:
    project_id (str): The ID of the Google Cloud project.
    zone (str): The zone where the VM instance will be created.
    instance_name (str): The name of the new VM instance.
    machine_type (str): The machine type for the new VM instance (e.g., 'n1-standard-1').
    image_name (str): The name of the custom image to use for the VM instance.

    Returns:
    The operation result of creating the VM instance.
    """
    instance_client = compute_v1.InstancesClient()
    instance = compute_v1.Instance()
    instance.name = instance_name
    instance.machine_type = f"zones/{zone}/machineTypes/{machine_type}"

    disk = compute_v1.AttachedDisk()
    disk.initialize_params = compute_v1.AttachedDiskInitializeParams()
    disk.initialize_params.source_image = image_name
    disk.auto_delete = True
    disk.boot = True
    instance.disks = [disk]

    network_interface = compute_v1.NetworkInterface()
    network_interface.name = "global/networks/default"  # Use appropriate VPC
    instance.network_interfaces = [network_interface]

    operation = instance_client.insert(
        project=project_id, zone=zone, instance_resource=instance
    )
    return operation.result()


def stop_instance(project_id, zone, instance_name):
    """
    Stops a Google Compute Engine instance.

    Args:
    project_id (str): The ID of the Google Cloud project.
    zone (str): The zone of the instance.
    instance_name (str): The name of the instance to stop.

    Returns:
    The operation result of stopping the instance.
    """
    instance_client = compute_v1.InstancesClient()

    operation = instance_client.stop(
        project=project_id, zone=zone, instance=instance_name
    )

    return operation.result()


def assign_external_ip(project_id, zone, instance_name):
    """
    Assigns an ephemeral external IP to a Google Compute Engine instance.

    Args:
    project_id (str): The ID of the Google Cloud project.
    zone (str): The zone of the instance.
    instance_name (str): The name of the instance to which the external IP will be assigned.

    Returns:
    The operation result of updating the instance's network interface.
    """
    instance_client = compute_v1.InstancesClient()

    # Retrieve the instance
    instance = instance_client.get(
        project=project_id, zone=zone, instance=instance_name
    )

    # Find the network interface
    for network_interface in instance.network_interfaces:
        if not network_interface.access_configs:
            # Add an access config (external IP) to the instance
            access_config = compute_v1.AccessConfig(nat_ip="", network_tier="PREMIUM")
            network_interface.access_configs = [access_config]
            break

    # Perform the update
    operation = instance_client.update(instance=instance, project=project_id, zone=zone)
    return operation.result()


def create_firewall_rule(project_id, rule_name, network, ports):
    """
    Creates a firewall rule to allow incoming traffic on specified ports.

    Args:
    project_id (str): The ID of the Google Cloud project.
    rule_name (str): The name of the firewall rule.
    network (str): The network where the rule will be applied.
    ports (list of str): A list of port numbers to allow (e.g., ['80', '443'] for HTTP and HTTPS).

    Returns:
    The operation result of inserting the firewall rule.
    """
    firewall_client = compute_v1.FirewallsClient()
    firewall = compute_v1.Firewall()
    firewall.name = rule_name
    firewall.direction = compute_v1.Firewall.Direction.INGRESS
    firewall.allowed = [{"IPProtocol": "tcp", "ports": ports}]
    firewall.network = network

    operation = firewall_client.insert(project=project_id, firewall_resource=firewall)
    return operation.result()


# project_id = 'your-project-id'
# rule_name = 'allow-http-https'
# network = 'global/networks/default'
# ports = ['80', '443']

# create_firewall_rule(project_id, rule_name, network, ports)
