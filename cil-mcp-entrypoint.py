#!/usr/bin/env python3
import os, sys

# Ensure cil is importable
sys.path.insert(0, "/Users/iamefe/Documents/projects/cil/src")
os.chdir("/Users/iamefe/Documents/projects/cil")

from cil.mcp.server import create_mcp_server

if __name__ == "__main__":
    server = create_mcp_server(use_sqlite=True)
    server()
