import { ButtonItem, PanelSection, PanelSectionRow, staticClasses } from "@decky/ui";
import {
  addEventListener,
  callable,
  definePlugin,
  removeEventListener,
  toaster,
} from "@decky/api";
import { useState } from "react";
import { FaGithub } from "react-icons/fa";

const ping = callable<[], string>("ping");
const emitDemoEvent = callable<[], void>("emit_demo_event");

function Content() {
  const [result, setResult] = useState<string>("Call Python backend");

  const onPing = async () => {
    const value = await ping();
    setResult(value);
  };

  const onEmit = async () => {
    await emitDemoEvent();
  };

  return (
    <PanelSection title="GitHub Sideload">
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={onPing}>
          {result}
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={onEmit}>
          Emit backend event
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}

export default definePlugin(() => {
  const listener = addEventListener<[message: string]>("demo_event", (message) => {
    toaster.toast({
      title: "GitHub Sideload",
      body: message,
    });
  });

  return {
    name: "GitHub Sideload",
    titleView: <div className={staticClasses.Title}>GitHub Sideload</div>,
    content: <Content />,
    icon: <FaGithub />,
    onDismount() {
      removeEventListener("demo_event", listener);
    },
  };
});
