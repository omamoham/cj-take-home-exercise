# Networking Design Questions

1. How do you avoid IP exhaustion given thousands of pods on a `/24`, without consuming more of `10.0.0.0/8`?

I would add an additional secondary ipv4 CIDR block to Cluster 1 VPC and Cluster 2 VPC.
You could add multple CIDRs to the same VPC in AWS. 
This way I could add 100.1.0.0/16 to CLuster 1 VPC and 100.2.0.0/16 to Cluster 2 VPC. Giving each Cluster vpc and additonal 65,536 ips for pods.
By this method, I could totally surpass using any additional IPS from the corporate network, by creating an additional secret unrelated CIDR to the corporate 10.0.0.0/8 range. You can create addititional subnets in these new CIDR ranges as you like for these pods that will use ips from the new secret CIDR ranges.

2. What is the exact Kubernetes/CNI mechanism that makes your IP plan work — not just "add a CIDR," but what does the VPC CNI need in order to actually assign pod IPs from it?

For simplification and undertsanding better we will just use Cluster 1 VPC 10.0.1.0/24 for this explaination.
Just adding a secondary CIDR to the VPC doesn't solve the problem of ipv4 exhaustion. The VPC CNI still hands out pod IPs from whatever subnet the node's primary ENI is in which is 10.0.1.0/24 in Cluster 1. You have to explicitly mention the VPC CNI to look elsewhere, and that's where `custom networking` comes into play.
The ENIConfig custom resource. This is what VPC CNI actually watches for. You create one per AZ, and each one says "pods in this zone get IPs from this subnet" pointing at your secondary CIDR subnet.
Update the Security group for the cluster 1 to allow traffic from all resources using the same security group, to allow for communication within the vpc.
Write one ENICONFIG resource per az pointing to the secondary CIDR subnet living in that AZ and also add the security group of the cluster 1 to each of the ENICONFIG. Deploy the maifests to your cluster.
Then set 2 environment variables in the aws-node daemonset.

```bash
kubectl set env daemonset aws-node -n kube-system AWS_VPC_K8S_CNI_CUSTOM_NETWORK_CFG=true \
ENI_CONFIG_LABEL_DEF=topology.kubernetes.io/zone
```
AWS_VPC_K8S_CNI_CUSTOM_NETWORK_CFG=true `turns the feature on`
ENI_CONFIG_LABEL_DEF=topology.kubernetes.io/zone `tells CNI which node label to check so it knows which ENIConfig to use`

So to summarize, 1. create a secondary CIDR in each vpc.
                 2. create subnets in that new CIDR in each AZ.
                 3. Create an ENICONFIG resource for each AZ and specify the secondary CIDR subnet in that az in them.
                 4. Set the 2 environement variables specified above in the aws-node daemonset.



3. How do Cluster 1 pods reach Cluster 2 endpoints without traversing the VPN, including what has to change beyond "connect the two VPCs"?

You implement vpc peering between the two vpc's so that resources residing in each vpc can communicate with resources in the other vpc directly without ever traversing through the corporate network. But just creating a peering connection is not it.

You need to add routes to the route table of Cluster 1 vpc and Cluster 2 vpc.
You first need to specify the route that traffic from cluster 1 VPC can travel to the primary and secondary CIDR of VPC 2 through the vpc peering connection.
You then need to do the same for the route table for Cluster 2 Vpc. Add routes to allow traffic from cluster 2 VPC to the primary and secondary cidr of cluster 1 vpc through the peering connection.

Lastly update the inbound rules of the security groups of both the clusters to allow traffic from primary and secondary cidr of the other cluster.


4. How are Cluster 2's endpoints exposed to the corporate network?
The cluster 2's endpoints are still exposed through the primary cidr of the cluster 2 vpc 10.0.2.0/24 . The pods lives and have endpoints in the secondary cidr, but the application load balancer ENI still resides in the primary CIDR. The ALB ingress will proxy the traffic to the target services which reside in the secondary CIDR.The ALB has an endpoint in the primary CIDR so the corporate network will only ever see thar endpoint. But the routing will be done by the alb secretly. To make things convenient and more manageable you can have a Route 53 private hosted zone associated with the VPC, so it resolves to the ALB's private DNS.

5. What are the assumptions, tradeoffs, or failure modes in your design?
Assumptions:
AZ count is fixed and permenant. In case a new subnet is created to be used we would need to create a new ENICONFIG for that AZ. If we didn't do this pods would fail to schedule.
We assumed the primary CIDR 10.0.1.0/24 has sufficient ips to even implement this solution. Some of the resources like ALB and nodes will still use primary CIDR ips.

Tradeoffs:
Enabling custom networking reduces max pods per node. We need to enable prefix delegation to increase the max pods per node back to previous amounts, but this takes additional operational overhead.
Enabling Custom networking is something disruptive on an existing cluster. It's better to orchestrate a parallel cluster and move traffic over gradually, requiring additional cost and operational overhead.

Faliure modes in Design:
While implementing this on my personal account I found out that, getting security groups and route tables misconfigured is very easy. Forgetting a rule or adding a route can leave the services broken.
Implementation of authentication is a must, as it will provide an additional layer of security.