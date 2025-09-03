package o2.policy

# default is deny
default allow = false

# allow tenantA to use the "echo" tool
allow {
  input.tenant == "tenantA"
  input.tool_id == "echo"
}
