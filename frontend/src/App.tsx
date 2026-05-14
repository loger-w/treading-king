import { useEffect } from "react";
import { Monitor } from "./pages/Monitor";

export default function App() {
  useEffect(() => {
    document.body.classList.add("opacity-100");
  }, []);

  return <Monitor />;
}
