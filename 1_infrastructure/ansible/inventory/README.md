# NHDW deploy inventories

An inventory declares the machines NHDW deploys onto. The playbook and roles are the same for one machine or many; only the inventory changes. The inventories of real deployment hosts are kept outside this repository.

## Inventories

| File | Shape |
|---|---|
| `single-node.yml` | One machine (`localhost`, `ansible_connection: local`). The control-plane node also runs all workloads; `workers` is empty. `nhdw_topology: single`. |
| `fleet.example.yml` | Template for one k3s server and three agents reached over SSH, with placeholder addresses. Copy it to `fleet.yml` and fill in the real hosts. `nhdw_topology: fleet`. |

## Contract

Every inventory defines two host groups and a few variables:

| Field | Meaning |
|---|---|
| `control_plane` (group) | Hosts that run the k3s server. One normally; three (an odd number) for an HA control plane. The deploy, seed and verify roles run once from the first of them. |
| `workers` (group) | Hosts that run the k3s agent and join the server by URL and node token. Empty on a single node. |
| `nhdw_topology` | `single` or `fleet`. Selects `helm/nhdw/values-<topology>.yaml` (replicas, storage class, resource requests). |
| `nhdw_cluster_name` | Cluster name (`nhdw`). |
| `nhdw_host_base_inventory` | Optional ingress host base. Without it the deploy uses `127.0.0.1.nip.io`. |
| `nhdw_node_ip` | Optional per-host node IP; defaults to the host's primary IPv4. |
| `ansible_host`, `ansible_user`, `ansible_ssh_private_key_file` | How to reach a remote host over SSH. Not needed for `localhost`. |

Local and remote hosts differ only in their connection settings.

## Usage

```
cd 1_infrastructure/ansible
ansible-playbook -i inventory/single-node.yml site.yml   # this machine (also the ansible.cfg default)
ansible-playbook -i inventory/fleet.yml site.yml         # a fleet defined from fleet.example.yml
```

The playbook installs k3s across the inventory (server and agents), installs the CNI, deploys the workloads with the topology's values profile, seeds the system and verifies it.
