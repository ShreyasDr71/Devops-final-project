variable "region" {
  description = "AWS region"
  default     = "ap-south-1"
}

variable "instance_type" {
  description = "m7i-flex.large (2 vCPU, 8 GB) is free-plan eligible and fits Jenkins + SonarQube"
  default     = "m7i-flex.large"
}

variable "instance_id" {
  description = "The EC2 instance launched from the console"
  default     = "i-00759236612560d57"
}

variable "ami" {
  description = "Image the instance was launched from (Ubuntu 26.04)"
  default     = "ami-0c7632fb77f35db43"
}

variable "key_name" {
  description = "Name of the EC2 key pair (the .pem file)"
  default     = "devops-project"
}

variable "private_key_path" {
  description = "Path to your .pem file, written into the Ansible inventory"
  default     = "~/.ssh/devops-project.pem"
}

variable "my_ip" {
  description = "Your IP in CIDR form for SSH access, e.g. 49.37.10.20/32"
  default     = "0.0.0.0/0"
}
