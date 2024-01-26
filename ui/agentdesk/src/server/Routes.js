import React from "react";
import { Route, Routes } from "react-router-dom";

import DesktopPage from "../pages/DesktopPage";

export default function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<DesktopPage />} />
    </Routes>
  );
}
