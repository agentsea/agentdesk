import { Nav } from "./Nav";

export default function Layout({ children }) {
  return (
    <div className="flex flex-col mx-24">
      <div>
        <Nav />
      </div>
      {children}
    </div>
  );
}
