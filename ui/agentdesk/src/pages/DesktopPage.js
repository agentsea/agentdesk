import React, { useRef } from "react";
import { VncScreen } from "react-vnc";
import Layout from "../components/Layout";
import { useLocation } from "react-router-dom";

export default function DesktopPage() {
  const location = useLocation();
  const queryParams = new URLSearchParams(location.search);
  const vncAddr = queryParams.get("vncAddr") || "ws://localhost:6080";

  const ref = useRef();

  // const agentdAddr = "http://localhost:8000";

  return (
    <Layout>
      <div className="flex flex-row mt-24 gap-6">
        <div className="border border-black flex w-fit h-fit shadow-2xl">
          <VncScreen
            url={vncAddr}
            scaleViewport
            background="#000000"
            style={{
              width: "960px",
              height: "600px",
            }}
            ref={ref}
          />
        </div>
      </div>
    </Layout>
  );
}
