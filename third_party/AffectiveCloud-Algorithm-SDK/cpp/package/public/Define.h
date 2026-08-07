#ifndef AFFECTIVE_ALGORITHM_DEFINE_EXPORT_H
#define AFFECTIVE_ALGORITHM_DEFINE_EXPORT_H
#include <vector>


struct ArousalReportRes
{
    std::vector<double> arousalRec; //激活度全程记录
    double arousalAvg; //激活度平均值
};

struct CoherenceReportRes
{
    std::vector<double> coherenceRec; //和谐度全程记录
	double coherenceAvg;  //和谐度平均值
    std::vector<int> flagRec; //佩戴标志全程记录
    int coherenceDuration; //和谐度时长
};

struct PleasureReportRes
{
    std::vector<double> pleasureRec; //愉悦度全程记录
	double pleasureAvg;    //愉悦度平均值
};

struct PressureReportRes
{
    std::vector<double> pressureRec; //压力水平全程记录
	double pressureAvg;            //压力水平平均值
};

struct RelaxationReportRes
{
    std::vector<double> relaxationRec; //放松度全程记录
	double relaxationAvg; //放松度平均值
};

struct AttentionReportRes
{
    std::vector<double> attentionRec; //放松度全程记录
	double attentionAvg; //放松度平均值
};

struct SleepReportRes
{
    // sleep stage curve
    std::vector<double> sleepCurve;
    //睡眠阶段
    // WAKE = 0,  // 清醒
    // NREM1 = 1,  // 非快速眼动期1（思睡期）
    // NREM2 = 2,  // 非快速眼动期2（浅睡期）
    // NREM3 = 3,  // 非快速眼动期3（深睡期）
    // REM = 4,  // 快速眼动期
    std::vector<int> sleepStage;
    int sleepPoint; //入睡点
    int sleepLatency; //进入入睡状态
    int awakeDuration; //清醒时长
    int remDuration; //快速眼动时长
    int lightDuration; //浅睡时长
    int deepDuration; //深睡时长

    // EEG curve
    std::vector<double> eegAlphaCurve;
    std::vector<double> eegBetaCurve;
    std::vector<double> eegThetaCurve;
    std::vector<double> eegDeltaCurve;
    std::vector<double> eegGammaCurve;
    std::vector<double> eegHighBetaDBCurve;
    std::vector<double> eegLowBetaDBCurve;
    std::vector<int> eegQualityRec;

    // movement and arousal
    int movementCount; //运动次数
    int arousalCount; //惊醒次数
    double disturbTolerance; //容差
    std::vector<int> movementRec;
    std::vector<int> arousalRec;
    std::vector<int> spindleRec;
};



struct FlowReportRes
{
    double flowAvg;      // 冥想度平均值
    std::vector<double>  flowRec;     // 冥想度全程记录
    std::vector<double>  flowTipsRec; // 冥想状态提示全程记录
    double flowPercent;        // 心流状态占比
    double flowDuration;       // 心流状态时长
    double flowLatency;        // 进入心流状态的用时
    int flowCombo;          // 心流状态最大连续时间
    double flowDepth;          // 心流状态最大深度
    int flowBackNum;        // 心流状态恢复提示次数
    int flowLossNum;        // 心流状态丢失提示次数
};

//心率报表
struct HRReportRes
{
    std::vector<int> hrRec;
    std::vector<double> hrvRec;
};

// 脑电报表
struct EEGReprotRes
{
    std::vector<double> eegAlphaRec;
    std::vector<double> eegBetaRec;
    std::vector<double> eegThetaRec;
    std::vector<double> eegDeltaRec;
    std::vector<double> eegGammaRec;
    std::vector<double> eegLowBetaRec;
    std::vector<double> eegHighBetaRec;
    std::vector<int> eegQualityRec;
};

struct SCEEGReportRes
{
    std::vector<double> eegAlphaRec; //脑电α波能量全程变化曲线
    std::vector<double> eegBetaRec;  //脑电β波能量全程变化曲线
    std::vector<double> eegThetaRec; //脑电θ波能量全程变化曲线
    std::vector<double> eegDeltaRec; //脑电δ波能量全程变化曲线
    std::vector<double> eegGammaRec; //脑电γ波能量全程变化曲线
    std::vector<int> eegQualityRec; //脑电质量等级全程变化曲线
};

struct PEPRReportRes
{
    int hrAvg; // 心率平均值
    int hrMax; // 心率最大值
    int hrMin; // 心率最小值
    std::vector<int> hrRec; // 心率全程记录
    std::vector<int> rrRec; // 呼吸率全程记录
    int rrAvg; // 呼吸率平均值
    std::vector<double> hrvRec; // 心率变异性全程记录
    double hrvAvg; // 心率变异性平均值
    // 脉搏波信号质量全程记录 0: NONE 1: POOR 2: NORM
    std::vector<int> bcgQualityRec;
    // 呼吸波信号质量全程记录 0: NONE 1: POOR 2: NORM
    std::vector<int> rwQualityRec;
};




//实时睡眠
struct SleepTriggerRes
{
    bool updateFlag;   //是否更新
    double sleepDegree;  //睡眠程度
    int sleepState;     //睡眠状态
    //睡眠阶段
    // WAKE = 0,  // 清醒
    // NREM1 = 1,  // 非快速眼动期1（思睡期）
    // NREM2 = 2,  // 非快速眼动期2（浅睡期）
    // NREM3 = 3,  // 非快速眼动期3（深睡期）
    // REM = 4,  // 快速眼动期
    int sleepStage;   
    double sleepSpindle; //睡眠抗干扰
};

//实时心流
struct FlowTriggerRes
{
    double meditation;     // 冥想程度
    double meditationTips; // 冥想状态提示
};

//实时心率
struct HRTriggerRes
{
    int hr;
    double hrv;
};

//实时脑电
struct EEGTriggerRes
{
    std::vector<double> eeglWave; //左脑波
    std::vector<double> eegrWave; //右脑波
    double eegAlphaPower;        //能量
    double eegBetaPower;
    double eegThetaPower;
    double eegDeltaPower;
    double eegGammaPower;
    double eegLowBetaPower;
    double eegHighBetaPower;
    double eegQuality;            //质量
};

struct SCEEGTriggerRes
{
    std::vector<double> eegWave; //脑波
    double eegAlphaPower;        //能量
    double eegBetaPower;
    double eegThetaPower;
    double eegDeltaPower;
    double eegGammaPower;
    double eegQuality;            //质量
};

struct PEPRAffectiveRes
{
    std::vector<double> bcgWave; //脉搏波波形
    std::vector<double> rwWave; //呼吸波波形
    // 脉搏波信号质量等级 0: NONE 1: POOR 2: NORM
    int bcgQuality;
    // 呼吸波信号质量等级 0: NONE 1: POOR 2: NORM
    int rwQuality;
    int hr; // 心率
    double hrv; // 心率变异性
    double rr; // 呼吸率
    double pressure; // 压力水平
    double coherence; // 和谐度
};

//单通道数据
struct SCEEGAffectiveRes
{
    double relaxation;
    SleepTriggerRes sleep;
    SCEEGTriggerRes eeg;

};

//实时的所有数据
struct AffectiveRes
{
    double relaxation;
    double pressure;
    double pleasure;
    double arousal;
    double coherence;
    double attention;
    SleepTriggerRes sleep;
    HRTriggerRes hr;
    EEGTriggerRes eeg;

};

struct EEGAffectiveRes
{
    EEGTriggerRes eeg;
    SleepTriggerRes sleep;
    double relaxation;
    double pleasure;
    double attention;
    FlowTriggerRes flow;
};

struct HRAffectiveRes
{
    HRTriggerRes hr;
    double pressure;
    double coherence;
    double arousal;
};

struct CSharpHRAffectiveRes
{
    double pressure;
    double coherence;
    double arousal;
    int hr;
    double hrv;
};

struct CSharpEEGAffectiveRes
{
    double relaxation;
    double pleasure;
    double attention;
    double eegAlphaPower;        //能量
    double eegBetaPower;
    double eegThetaPower;
    double eegDeltaPower;
    double eegGammaPower;
    double eegQuality;            //质量
    double sleepDegree;  //睡眠程度
    int sleepState;     //睡眠状态
};

struct CSharpSleepReportRes
{
    int sleepPoint;     //入睡点, 相对于全程记录的索引位置
    int sleepLatency;   //进入入睡状态
    int awakeDuration;  //清醒时长
    int lightDuration;  //浅睡时长
    int deepDuration;  //深睡时长
};

struct CSharpArousalReportRes
{
    double arousalAvg; //激活度平均值
};

struct CSharpCoherenceReportRes
{
	double coherenceAvg;  //和谐度平均值
};

struct CSharpPleasureReportRes
{
	double pleasureAvg;    //愉悦度平均值
};

struct CSharpPressureReportRes
{
	double pressureAvg;            //压力水平平均值
};

struct CSharpRelaxationReportRes
{
	double relaxationAvg; //放松度平均值
};

struct CSharpAttentionReportRes
{
	double attentionAvg; //放松度平均值
};
#endif
