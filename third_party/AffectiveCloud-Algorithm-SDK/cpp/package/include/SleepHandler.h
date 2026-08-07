#ifndef C88EFA30_901B_4A4D_AD85_7CA8FBEE3EF2
#define C88EFA30_901B_4A4D_AD85_7CA8FBEE3EF2
#include "AffectionData.h"
#include "NumCpp.hpp"
#include "Data.h"
#include "TypeDefine.h"
#include "SVM.h"

namespace basic::affection::handler
{
    struct SleepHandlerTemp
    {
        //自适应结束时间点
        int adjFinishTime;
        //睡眠状态
        define::SleepStateEnum sleepState;
        //睡眠潜伏期时间点
        int latencyTime;
        //入睡标志
        bool sleepFlag;
        //网络输出暂存序列
        nc::NdArray<double> modelDegreeStore;
        //睡眠相位暂存序列，用于判断睡眠状态
        nc::NdArray<int> sleepPhaseStore;
        //睡眠概率暂存序列，用于判断睡眠状态
        nc::NdArray<double> sleepProbStore;
        //睡眠状态长度，用于判断睡眠状态
        int sleepStateLen;
        //脑电信号质量暂存序列，用于判断睡眠状态
        nc::NdArray<int> eegQualityStore;
        //睡眠程度暂存序列，用于计算均值
        nc::NdArray<int> sleepStageStore;
        int sleepStage;
        //睡眠程度暂存序列，用于计算均值
        nc::NdArray<double> sleepDegreeStore;
        nc::NdArray<double> betaStore;
        nc::NdArray<double> thetaStore;
        nc::NdArray<double> deltaStore;
        nc::NdArray<double> gammaStore;

        svm_model *model;
    };

    struct SleepHandlerResult
    {
        double sleepDegree;
        define::SleepStateEnum sleepState;
        define::SleepStage sleepStage;
    };

    /// 睡眠处理器
    /// \param eegFeature 脑电特征
    /// \param eegPower 脑电能量
    /// \param eegQuality 脑电信号质量
    /// \param timeCum 累计运行时长
    /// \param tmp 缓存
    /// \param smoothScope 睡眠程度平滑范围
    /// \param adjScope 自适应范围（网络分类阈值基线选取范围）
    /// \return
    SleepHandlerResult sleepHandler(
            nc::NdArray<double> eegFeature, dsp::EEGPower& eegPower,
            int eegQuality, double timeCum, SleepHandlerTemp &tmp ,int smoothScope = 10, int adjScope = 10);
}

#endif /* C88EFA30_901B_4A4D_AD85_7CA8FBEE3EF2 */
