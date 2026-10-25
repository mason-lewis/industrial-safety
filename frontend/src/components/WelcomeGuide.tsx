import Dialog from './ReviewDialog';
import Icon from './ReviewIcon';

const welcomeKey = 'industrial-safety.welcome-seen.v1';
export function shouldShowWelcome() {
  try { return localStorage.getItem(welcomeKey) !== 'yes'; }
  catch { return true; }
}
export function rememberWelcome() {
  try { localStorage.setItem(welcomeKey, 'yes'); }
  catch { /* Closing the guide still works without browser storage. */ }
}

export default function WelcomeGuide({onClose}: {onClose: () => void}) {
  return <Dialog title="Welcome" onClose={onClose}>
    <div className="welcome-guide">
      <p className="guide-intro" tabIndex={-1} data-dialog-autofocus>Three steps. No setup.</p>
      <ol className="guide-steps">
        <li><span><Icon name="upload"/></span><div><strong>Drop</strong><small>Open a video or pick one from your library.</small></div></li>
        <li><span><Icon name="eye"/></span><div><strong>Watch</strong><small>Findings appear beside the video with their evidence.</small></div></li>
        <li><span><Icon name="report"/></span><div><strong>Report</strong><small>Decide on each finding, then download.</small></div></li>
      </ol>
      <div className="guide-modes">
        <div><span><Icon name="eye"/></span><div>Preview<small>Local person boxes · no AI</small></div></div>
        <div><span><span className="tab-dot"/></span><div>Live<small>AI reviews as it plays</small></div></div>
        <div><span><Icon name="video"/></span><div>Review<small>AI checks the whole video</small></div></div>
      </div>
      <details className="detail-disclosure guide-control"><summary>AI, data and limits <Icon name="chevron"/></summary><div>
        <p>Opening a video in Live or Review starts AI analysis. Preview uses local CV only and works with AI off. The confidence slider updates Preview and new reviews; saved runs keep their settings.</p>
        <p>Your video is stored locally. Live and Review send sampled images to the AI provider. Limits stay at $0.15 per review, $2 per day and $20 per month. Recorded Review also has an Ask tab: only Send starts a question, with its own $0.05 maximum. Ask uses visual frames; audio is not assessed.</p>
        <p>Set any required protective equipment and safety references under Review settings before opening a video.</p>
      </div></details>
      <div className="guide-actions"><span>Reopen anytime from <strong>How to use</strong>.</span><button className="solid-button" onClick={onClose}>Get started <Icon name="arrow"/></button></div>
    </div>
  </Dialog>;
}
