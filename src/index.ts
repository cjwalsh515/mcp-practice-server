import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const server = new McpServer({
  name: "mcp-practice-server",
  version: "1.0.0",
});

server.registerTool(
  "ping",
  {
    description: "Connectivity test tool. Takes no input and always returns a fixed response.",
    inputSchema: {},
  },
  async () => ({
    content: [{ type: "text", text: "pong" }],
  }),
);

const transport = new StdioServerTransport();
await server.connect(transport);
