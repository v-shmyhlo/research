from all_the_tools.config import Config as C

epochs = 1000
batch_size = 32

config = C(
    seed=42,
    epochs=epochs,
    epochs_warmup=int(epochs * 0.1),
    log_interval=int(epochs * 0.1),
    model='resnet34',
    train=C(
        num_labeled=4000,
        batch_size=batch_size,
        weight_u=75.,
        temp=0.5,
        alpha=0.75,
        opt=C(
            type='sgd',
            lr=2e-3,
            momentum=0.9,
            weight_decay=1e-4),
        sched=C(
            type='warmup_cosine')),
    eval=C(
        batch_size=batch_size))
