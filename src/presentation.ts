// Demo geometry on native Decky controls; colors come from Steam's live theme tokens.
import { gamepadDialogClasses as field } from "@decky/ui";

export const presentationStyles = `
.Panel:has(>div>.decky-sideloader-layout){display:flex;flex-direction:column;flex:1;min-height:0;height:100%;min-width:0}
.Panel:has(>div>.decky-sideloader-layout)>:first-child{flex:none}
div:has(>.decky-sideloader-layout){display:flex;flex-direction:column;flex:1;min-height:0;box-sizing:border-box}
.decky-sideloader-layout{
  --gss-muted:var(--color-greyneutral-12);
  --gss-accent:var(--color-blue-11);
  --gss-border:color-mix(in srgb,currentColor 9%,transparent);
  display:flex;flex-direction:column;flex:1;min-height:0;min-width:0;max-width:100%;height:100%;
  color:var(--color-slate-12);font-size:14px;line-height:1.5;
}
.decky-sideloader-layout *{box-sizing:border-box}
.decky-sideloader-body{min-height:0;flex:1;display:flex;overflow:hidden}
.decky-sideloader-body>.Panel{min-height:0;flex:1;overflow-y:auto;overflow-x:hidden;scrollbar-width:none;padding:0 0 30px}
.decky-sideloader-body>.Panel::-webkit-scrollbar{display:none}
.gss-home .decky-sideloader-body>.Panel{padding-top:23px}
.decky-sideloader-layout .gss-section>div{padding:0;margin:0 0 24px;border:0;background:none}
.decky-sideloader-layout .gss-section[data-titled=true]>div>:first-child{padding:0 16px;margin:0 0 9px;font-size:11px;line-height:1.5;letter-spacing:.6px;color:var(--gss-muted);font-weight:500;text-transform:none}
.decky-sideloader-layout .gss-section[data-titled=true]>div>:first-child>div{font:inherit;color:inherit;padding:0;margin:0}
.decky-sideloader-layout .decky-sideloader-body .${field.Field}{--field-negative-horizontal-margin:0px;--field-row-children-spacing:8px;padding:12px 16px;margin:0;min-width:0;border-radius:2px}
.decky-sideloader-layout .${field.FieldLabel}{font-size:14px;line-height:1.5;font-weight:450;overflow-wrap:anywhere}
.decky-sideloader-layout .${field.FieldDescription}{font-size:12px;line-height:1.65;color:var(--gss-muted);margin-top:4px;overflow-wrap:anywhere}
.decky-sideloader-layout .${field.FieldLeftColumn}{min-width:0}
.decky-sideloader-layout .${field.FieldIcon}{margin-right:12px;flex:none}
.decky-sideloader-layout .${field.FieldChildrenInner}{min-width:0}
.decky-sideloader-layout .${field.FieldChildrenWithIcon}{min-width:0}
.decky-sideloader-layout h2,.decky-sideloader-layout h3,.decky-sideloader-layout p{margin:0}
.decky-sideloader-layout .decky-sideloader-body .gss-heading.${field.Field}{display:block;min-height:0;padding:0;margin:0;border:0;background:none}
.decky-sideloader-layout .gss-hero{margin:0 0 21px;padding-inline:16px}
.decky-sideloader-layout .gss-hero h2{font-size:23px;line-height:1.35;font-weight:650;letter-spacing:-.5px;margin:5px 0 6px;overflow-wrap:anywhere}
.decky-sideloader-layout .gss-hero p{font-size:13px;line-height:1.65;color:var(--gss-muted);overflow-wrap:anywhere}
.decky-sideloader-layout .gss-plugin-hero{display:flex;align-items:center;gap:12px;margin-bottom:23px}
.decky-sideloader-layout .gss-plugin-hero>div{min-width:0}
.decky-sideloader-layout .gss-plugin-hero h2{font-size:20px}
.decky-sideloader-layout .gss-plugin-hero p{font-size:12px}
.decky-sideloader-layout .gss-hero-icon{display:grid;place-items:center;flex:none;width:36px;height:36px;border-radius:7px;background:color-mix(in srgb,var(--gss-accent) 15%,transparent);color:var(--color-blue-12)}
.decky-sideloader-layout .gss-hero-icon svg{width:20px;height:20px}
.decky-sideloader-layout .gss-check-line{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px;margin:0 0 19px;padding-inline:16px;color:var(--gss-muted);font-size:11px}
.decky-sideloader-layout .gss-check-line>span{flex:1;min-width:0}
.decky-sideloader-layout .gss-plugin-group{position:relative}
.decky-sideloader-layout .gss-section-count{position:absolute;top:0;right:16px;font-size:11px;color:var(--gss-muted)}
/* Override Steam's three-class Field padding without changing other native fields. */
.decky-sideloader-layout .decky-sideloader-body .gss-row.${field.Field}{padding:12px 16px;min-height:60px;border-bottom:1px solid var(--gss-border);align-items:center}
.decky-sideloader-layout .gss-row .${field.FieldLabel}{font-size:15px;font-weight:550}
.decky-sideloader-layout .gss-row .${field.FieldDescription}{margin-top:3px;font-size:12px;line-height:1.5}
.decky-sideloader-layout :is(.gss-row,.gss-status) .${field.FieldLeftColumn}{position:relative}
.decky-sideloader-layout :is(.gss-row,.gss-status) .${field.FieldLeftColumn}:has(.${field.FieldIcon}){padding-left:48px}
.decky-sideloader-layout :is(.gss-row,.gss-status) .${field.FieldIcon}{position:absolute;left:0;top:50%;transform:translateY(-50%);margin:0;padding:0;width:36px;height:36px;display:grid;place-items:center}
.decky-sideloader-layout :is(.gss-row,.gss-status) .${field.FieldIcon}>svg{width:20px;height:20px}
.decky-sideloader-layout .gss-row [data-plugin-icon]{width:36px;height:36px;border-radius:7px;align-items:center;justify-content:center;background:color-mix(in srgb,var(--gss-accent) 14%,transparent);color:var(--color-blue-12)}
.decky-sideloader-layout .gss-row [data-plugin-icon] svg{width:20px;height:20px}
.decky-sideloader-layout .decky-sideloader-body .gss-action-row.${field.Field}{min-height:44px;padding:8px 16px}
.decky-sideloader-layout .gss-action-row .${field.FieldLabel}{font-size:14px;font-weight:450}
.decky-sideloader-layout .gss-row-icon{width:36px;height:36px;display:grid;place-items:center;color:var(--gss-muted)}
.decky-sideloader-layout .gss-row-icon svg{width:20px;height:20px}
.decky-sideloader-layout .gss-chevron{width:14px;height:14px;color:var(--gss-muted)}
.decky-sideloader-layout .gss-settings .gss-action-row .${field.FieldLeftColumn}:has(.${field.FieldIcon}){padding-left:28px}
.decky-sideloader-layout .gss-settings .gss-action-row :is(.${field.FieldIcon},.gss-row-icon){width:16px;height:16px}
.decky-sideloader-layout .gss-settings .gss-action-row .${field.FieldIcon} svg{width:16px;height:16px}
/* Every page is edge-to-edge; only content and controls own their horizontal inset. */
.decky-sideloader-layout :is(.gss-note,.gss-release-label,.gss-version,.gss-release-meta,.gss-release-notes,.gss-home-actions,.gss-language){padding-inline:16px}
.decky-sideloader-layout button.DialogButton.gss-button{display:flex;align-items:center;justify-content:center;gap:9px;min-width:0;width:calc(100% - 32px);min-height:46px;padding:11px 13px;margin:10px 16px 0;border-radius:3px;font-size:14px;line-height:1.5;font-weight:600;white-space:normal;overflow-wrap:anywhere}
.decky-sideloader-layout .gss-button>svg{flex:none}
.decky-sideloader-layout button.DialogButton.gss-button-quiet,.decky-sideloader-layout button.DialogButton.gss-button-danger{background:color-mix(in srgb,currentColor 5%,transparent);font-size:13px;font-weight:500;min-height:43px}
.decky-sideloader-layout button.DialogButton.gss-button-inline{background:transparent;color:var(--gss-accent);border:0;box-shadow:none;width:100%;min-height:40px;padding:8px 16px;margin:0;font-size:12px;font-weight:450;justify-content:flex-start;text-align:left;gap:12px}
.decky-sideloader-layout .gss-button-inline>svg{width:20px;height:20px;margin-inline:8px}
.decky-sideloader-layout .gss-check-line button.DialogButton{width:auto;min-height:32px;padding:6px 10px;line-height:20px;flex:none;gap:9px}
.decky-sideloader-layout .gss-check-line .gss-button>svg{width:14px;height:14px;margin:0}
/* Keep this compact action's focus area legible despite global theme pill/ring overrides. */
#QuickAccess-Menu .decky-sideloader-layout .gss-check-line button.DialogButton{border-radius:4px!important}
#QuickAccess-Menu .decky-sideloader-layout .gss-check-line button.DialogButton:is(.gpfocus,:focus-visible){background-color:var(--color-slate-12)!important;color:var(--color-greyneutral-6)!important;box-shadow:none!important;outline:none}
.decky-sideloader-layout button.DialogButton.gss-icon-button{width:auto;min-width:0;min-height:0;padding:6px;margin:0}
.decky-sideloader-layout .gss-back button.DialogButton{min-height:0;height:auto;padding:8px 16px;margin:0;color:var(--gss-muted)}
.decky-sideloader-layout .gss-back .gss-button-inline>svg{width:16px;height:16px}
.decky-sideloader-layout .gss-danger,.decky-sideloader-layout button.gss-button-danger{color:var(--color-red-11)}
.decky-sideloader-layout .decky-sideloader-body .gss-status.${field.Field}{padding:8px 16px;min-height:44px}
.decky-sideloader-layout .gss-status .${field.FieldLabel}{font-size:13px}
.decky-sideloader-layout .gss-operation-steps .${field.FieldLabel}{display:block}
.decky-sideloader-layout .gss-step-dots{display:flex;align-items:center;list-style:none;padding:4px 0;margin:0 0 9px;width:100%}
.decky-sideloader-layout .gss-step-dots li{display:flex;align-items:center;flex:1;min-width:0;color:var(--gss-accent)}
.decky-sideloader-layout .gss-step-dots li:last-child{flex:0 0 8px}
.decky-sideloader-layout .gss-step-dots li::before{content:"";display:block;flex:none;width:8px;height:8px;border-radius:50%;background:currentColor}
.decky-sideloader-layout .gss-step-dots li:not(:last-child)::after{content:"";height:2px;flex:1;margin:0 4px;background:var(--gss-border)}
.decky-sideloader-layout .gss-step-dots li[data-state=past]::after{background:currentColor}
.decky-sideloader-layout .gss-step-dots li[data-state=future]{color:var(--gss-border)}
.decky-sideloader-layout .gss-step-dots[data-result=error] li[data-state=current]{color:var(--color-red-11)}
.decky-sideloader-layout .gss-step-dots[data-result=stopped] li[data-state=current]{color:var(--color-yellow-11)}
.decky-sideloader-layout .gss-step-dots:not([data-result=success]) li[data-state=current]::before{box-shadow:0 0 0 3px color-mix(in srgb,currentColor 25%,transparent)}
.decky-sideloader-layout .gss-step-label{display:block;overflow-wrap:anywhere}
.decky-sideloader-layout .decky-sideloader-body :is(.gss-status-warning,.gss-status-error).${field.Field}{border-left:2px solid var(--color-yellow-11);padding-left:14px;margin:18px 0}
.decky-sideloader-layout .decky-sideloader-body .gss-status-error.${field.Field}{border-left-color:var(--color-red-11)}
.decky-sideloader-layout .decky-sideloader-body .gss-skipped-notice .gss-status.${field.Field}{min-height:0;height:auto;padding-block:0;margin:0 0 16px}
.decky-sideloader-layout p.gss-note{font-size:11px;line-height:1.7;color:var(--gss-muted);margin:10px 0;overflow-wrap:anywhere}
.decky-sideloader-layout .gss-release-actions{display:flex;align-items:center;justify-content:flex-end;gap:8px;padding:0 16px;margin:8px 0 16px}
.decky-sideloader-layout .gss-release-primary{flex:1;min-width:0}
.decky-sideloader-layout .gss-release-primary button.DialogButton.gss-button{width:100%;min-height:36px;margin:0;padding:7px 10px;font-size:13px;line-height:20px}
.decky-sideloader-layout .gss-release-actions button.DialogButton.gss-icon-button{flex:none;width:36px;height:36px;min-height:36px;padding:8px;justify-content:center}
.decky-sideloader-layout .gss-release-actions .gss-icon-button>svg{width:16px;height:16px;margin:0}
.decky-sideloader-layout .gss-release-updated p.gss-note{flex:1;min-width:0;margin:0;padding:0}
.decky-sideloader-layout .gss-release-label{display:flex;justify-content:space-between;gap:8px;align-items:center;font-size:11px;color:var(--gss-muted);margin-bottom:9px}
.decky-sideloader-layout .gss-tag{font-size:10px;border:1px solid var(--gss-border);padding:2px 6px;border-radius:3px;flex:none}
.decky-sideloader-layout .gss-version{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin:12px 0 7px;font-size:25px;line-height:1.3;letter-spacing:-.8px;overflow-wrap:anywhere}
.decky-sideloader-layout .gss-version .gss-from{font-size:18px;color:var(--gss-muted)}
.decky-sideloader-layout .gss-version>svg{width:18px;height:18px;color:var(--gss-muted);flex:none}
.decky-sideloader-layout .gss-release-meta{display:grid;gap:4px;font-size:12px;line-height:18px;color:var(--gss-muted);margin:0 0 16px;overflow-wrap:anywhere}
.decky-sideloader-layout .gss-release-meta>div{display:flex;align-items:flex-start;gap:6px;min-width:0}
.decky-sideloader-layout .gss-release-meta svg{width:12px;height:12px;flex:none;margin-top:3px}
.decky-sideloader-layout .gss-release-meta span{min-width:0}
.decky-sideloader-layout .gss-release-notes{font-size:13px;line-height:1.7;overflow-wrap:anywhere;margin:14px 0 10px;color:var(--gss-muted)}
.decky-sideloader-layout .gss-release-notes-preview{max-height:180px;overflow:hidden;pointer-events:none}
.decky-sideloader-layout .gss-release-notes :is(h1,h2,h3,h4,h5,h6){color:var(--color-slate-12);font-size:16px;line-height:1.4;margin:18px 0 8px}
.decky-sideloader-layout .gss-release-notes :is(h1,h2){font-size:18px}
.decky-sideloader-layout .gss-release-notes :is(p,ul,ol,blockquote,pre,table){margin:0 0 12px}
.decky-sideloader-layout .gss-release-notes>:first-child{margin-top:0}
.decky-sideloader-layout .gss-release-notes :is(ul,ol){padding-left:20px}
.decky-sideloader-layout .gss-release-notes li+li{margin-top:4px}
.decky-sideloader-layout .gss-release-notes blockquote{padding-left:12px;border-left:2px solid var(--gss-border)}
.decky-sideloader-layout .gss-release-notes code{font-family:monospace;font-size:.92em;background:color-mix(in srgb,currentColor 8%,transparent);border-radius:3px;padding:1px 3px}
.decky-sideloader-layout .gss-release-notes pre{padding:10px;background:color-mix(in srgb,currentColor 5%,transparent);border:1px solid var(--gss-border);border-radius:3px;white-space:pre-wrap;overflow-wrap:anywhere}
.decky-sideloader-layout .gss-release-notes pre code{padding:0;background:none}
.decky-sideloader-layout .gss-release-notes img{display:block;max-width:100%;height:auto;margin:8px 0}
.decky-sideloader-layout .gss-release-notes table{width:100%;table-layout:fixed;border-collapse:collapse;font-size:12px}
.decky-sideloader-layout .gss-release-notes :is(th,td){border:1px solid var(--gss-border);padding:5px;text-align:left;overflow-wrap:anywhere}
.decky-sideloader-layout .gss-release-notes hr{border:0;border-top:1px solid var(--gss-border);margin:16px 0}
.decky-sideloader-layout .gss-release-notes .gss-markdown-link{color:var(--gss-accent);text-decoration:underline}
.decky-sideloader-layout .gss-release-notes button.DialogButton.gss-markdown-link{display:inline;min-width:0;max-width:100%;width:auto;min-height:0;padding:0;margin:0;border:0;border-radius:2px;background:none;font:inherit;line-height:inherit;text-align:inherit;white-space:normal;overflow-wrap:anywhere}
.decky-sideloader-layout .gss-release-notes .gss-markdown-block.${field.Field}{display:block;min-height:0;padding:0;margin:0;border:0;background:none}
.decky-sideloader-layout .gss-markdown-block .${field.FieldLabel}{font:inherit;color:inherit;white-space:normal}
.decky-sideloader-layout .gss-license-text{padding:0 16px;font-size:12px;line-height:1.7}
.decky-sideloader-layout .decky-sideloader-body .gss-license-paragraph.${field.Field}{display:block;min-height:0;padding:0;margin:0;border:0;background:none}
.decky-sideloader-layout .gss-license-paragraph .${field.FieldLabel}{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere}
.decky-sideloader-layout .gss-license-attribution{white-space:pre-wrap}
.decky-sideloader-layout .gss-version-row[aria-current=true]{background:color-mix(in srgb,var(--gss-accent) 5%,transparent)}
.decky-sideloader-layout .gss-choice{display:flex;align-items:flex-start;gap:12px;padding:15px 16px;width:100%;border-bottom:1px solid var(--gss-border);border-radius:2px;cursor:pointer}
.decky-sideloader-layout .gss-choice>span:last-child{min-width:0}
.decky-sideloader-layout .gss-choice strong{font-size:14px;font-weight:550;display:block}
.decky-sideloader-layout .gss-choice small{display:block;color:var(--gss-muted);font-size:12px;line-height:1.7;margin-top:4px;overflow-wrap:anywhere}
.decky-sideloader-layout .gss-radio{width:17px;height:17px;border:1px solid var(--color-greyneutral-10);border-radius:50%;margin-top:2px;flex:none;display:grid;place-items:center}
.decky-sideloader-layout .gss-choice[aria-checked=true]{background:color-mix(in srgb,var(--gss-accent) 5%,transparent)}
.decky-sideloader-layout .gss-choice[aria-checked=true] .gss-radio{border-color:var(--gss-accent)}
.decky-sideloader-layout .gss-choice[aria-checked=true] .gss-radio:after{content:"";width:9px;height:9px;background:var(--gss-accent);border-radius:50%}
.decky-sideloader-layout .gss-choice[aria-disabled=true]{opacity:.5;cursor:default}
/* Full-row actions use the native Field-like highlight, not a theme's button ring. */
#QuickAccess-Menu .decky-sideloader-layout :is(button.DialogButton.gss-button-inline,button.DialogButton.gss-disclosure-toggle,.gss-choice):not([data-action-key=checkUpdates]){border-radius:2px!important}
#QuickAccess-Menu .decky-sideloader-layout :is(button.DialogButton.gss-button-inline,button.DialogButton.gss-disclosure-toggle,.gss-choice):not([data-action-key=checkUpdates]):is(.gpfocus,.gss-focus,:focus-visible){background:color-mix(in srgb,var(--color-slate-12) 15%,transparent)!important;color:var(--color-slate-12)!important;box-shadow:none!important;outline:none!important}
.decky-sideloader-layout .gss-disclosure{margin-top:17px;border-top:1px solid var(--gss-border);border-bottom:1px solid var(--gss-border)}
.decky-sideloader-layout button.DialogButton.gss-disclosure-toggle{min-width:0;width:100%;display:flex;justify-content:space-between;align-items:center;gap:8px;background:transparent;box-shadow:none;border:0;border-radius:2px;padding:13px 16px;font-size:12px;white-space:normal;text-align:left}
.decky-sideloader-layout .gss-disclosure-body{padding:0 16px 13px;font-size:12px;line-height:1.8;color:var(--gss-muted);overflow-wrap:anywhere}
.decky-sideloader-layout .gss-disclosure-body:has(.gss-status){padding-inline:0}
.decky-sideloader-layout .gss-home-actions{display:flex;align-items:stretch;gap:8px;margin:0}
.decky-sideloader-layout :is(.gss-home-actions,.gss-task-actions) button.DialogButton{min-width:0;width:auto;min-height:43px;padding:10px 12px;border-radius:3px;flex:1;font-size:13px;line-height:1.5;white-space:normal;font-weight:500;overflow-wrap:anywhere;display:flex;align-items:center;justify-content:center;gap:8px}
.decky-sideloader-layout .gss-home-actions button.DialogButton:last-child{flex:0 1 auto;max-width:110px}
.decky-sideloader-layout .gss-task-notice{margin:0 0 20px;padding:0}
.decky-sideloader-layout .decky-sideloader-body .gss-task-notice .gss-row.${field.Field}{border:0}
.decky-sideloader-layout .gss-task-actions{display:flex;align-items:stretch;gap:8px;padding:0 16px}
.decky-sideloader-layout .${field.Field}:not(.gss-row):has(input,[role=combobox],[role=checkbox]){border-bottom:1px solid var(--gss-border)}
.decky-sideloader-layout .DialogInputLabelGroup :is(.DialogLabel,.DialogLabelExplainer){padding-inline:16px}
.decky-sideloader-layout input{min-width:0;max-width:100%}
.decky-sideloader-layout .gss-repository-input{padding:0 16px;margin:0 0 12px;min-width:0}
.decky-sideloader-layout .gss-repository-input .DialogInputLabelGroup :is(.DialogLabel,.DialogLabelExplainer){padding-inline:0}
.decky-sideloader-layout .gss-repository-input button.DialogButton.gss-icon-button{flex:none;width:36px;height:36px;padding:8px;margin:0 0 0 8px}
.decky-sideloader-layout .gss-repository-input .gss-icon-button>svg{width:16px;height:16px}
.decky-sideloader-layout [data-action-key]{overflow-wrap:anywhere}
.decky-sideloader-layout .gss-language{display:flex;flex-direction:column;align-items:flex-end;gap:4px}
.decky-sideloader-layout .gss-language-label{display:flex;align-items:center;gap:8px}
.decky-sideloader-layout .gss-language-label{font-size:12px;white-space:nowrap}
.decky-sideloader-layout .gss-language-label svg{width:20px;height:20px;flex:none}
`;
