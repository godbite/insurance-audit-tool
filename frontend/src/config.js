// Backend host address config dynamically derived from browser location
const hostname = window.location.hostname || "localhost";
const finalHostname = hostname === "localhost" ? "127.0.0.1" : hostname;
const protocol = window.location.protocol || "http:";
const wsProtocol = protocol === "https:" ? "wss:" : "ws:";

export const BACKEND_URL = `${protocol}//${finalHostname}:8000`;
export const WS_URL = `${wsProtocol}//${finalHostname}:8000`;

