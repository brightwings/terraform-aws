variable "function_name" {
  type = string
}

variable "source_dir" {
  description = "Path to directory containing handler.py"
  type        = string
}

variable "execution_role_arn" {
  type = string
}

variable "environment_variables" {
  type    = map(string)
  default = {}
}

variable "timeout" {
  type    = number
  default = 60
}

variable "memory_size" {
  type    = number
  default = 128
}

variable "tags" {
  type    = map(string)
  default = {}
}
