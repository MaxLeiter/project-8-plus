// Shared by server and client components (a "use client" module cannot export plain functions to the server).

/** Anchor id of a Lua API entry: "qb.call" -> "api-qb-call". */
export const apiId = (name: string) => `api-${name.replace(/[^a-zA-Z0-9]+/g, "-").replace(/-$/, "")}`;
