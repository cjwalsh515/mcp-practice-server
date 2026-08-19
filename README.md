# mcp-practice-server

A minimal [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server written in TypeScript, built with the official [MCP TypeScript SDK](https://github.com/modelcontextprotocol/typescript-sdk) and [Zod](https://zod.dev) for input validation.

It exposes exactly one tool, `ping`, which takes no meaningful input and always returns a fixed `"pong"` response. This project exists purely as a connectivity test — a simple way to verify that an MCP client can discover and call a tool on a server — not as a feature-complete server.

## Install

```bash
npm install
```

## Run locally

```bash
npm start
```

This builds the TypeScript source and starts the server, which communicates over stdio (standard input/output) using the MCP protocol.

To connect it to an MCP client, point the client at this command (e.g. `node dist/index.js`, after running `npm run build`), or configure it to run `npm start` in this directory.
