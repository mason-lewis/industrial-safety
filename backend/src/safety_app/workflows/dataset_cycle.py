"""Training and evaluation are separate user-requested jobs. Promotion is explicit."""
from safety_app.modules.labeling.train import train,register_model
from safety_app.modules.labeling.evaluate import evaluate_model
async def dataset_cycle(rt,job):
    if job.kind=='register':return await register_model(rt,job)
    if job.kind=='train':return await train(rt,job)
    return await evaluate_model(rt,job)
