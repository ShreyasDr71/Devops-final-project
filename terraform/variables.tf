variable "region" {
  description = "AWS region"
  default     = "ap-south-1"
}

variable "instance_type" {
  description = "m7i-flex.large (2 vCPU, 8 GB) is free-plan eligible and fits Jenkins + SonarQube"
  default     = "m7i-flex.large"
}

variable "key_name" {
  description = "Name of the EC2 key pair you created in the AWS console (the .pem file)"
}

variable "private_key_path" {
  description = "Path to your .pem file, written into the Ansible inventory"
  default     = "~/.ssh/cardea.pem"
}

variable "my_ip" {
  description = "Your IP in CIDR form for SSH access, e.g. 49.37.10.20/32"
  default     = "0.0.0.0/0"
}
