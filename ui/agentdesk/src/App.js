import "./App.css";
import React, { useRef } from "react";
import { VncScreen } from "react-vnc";
import { Nav } from "./components/Nav";

function App() {
  const ref = useRef();

  return (
    <div className="flex flex-col mx-24">
      <div>
        <Nav />
      </div>
      <div className="border border-black flex w-fit mt-24">
        <VncScreen
          url="ws://localhost:6080"
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
  );
}

export default App;
