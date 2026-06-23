const envBackendUrl = import.meta.env.VITE_BACKEND_URL;

const hostname = window.location.hostname || "localhost";
const finalHostname = hostname === "localhost" ? "127.0.0.1" : hostname;
const protocol = window.location.protocol || "http:";
const wsProtocol = protocol === "https:" ? "wss:" : "ws:";

// If VITE_BACKEND_URL is defined, use it. Otherwise, fallback to local dynamic host on port 8000.
export const BACKEND_URL = envBackendUrl 
  ? envBackendUrl.replace(/\/$/, "")
  : `${protocol}//${finalHostname}:8000`;

const getWsUrl = () => {
  if (envBackendUrl) {
    return envBackendUrl.replace(/^http/, "ws").replace(/\/$/, "");
  }
  return `${wsProtocol}//${finalHostname}:8000`;
};

export const WS_URL = getWsUrl();

