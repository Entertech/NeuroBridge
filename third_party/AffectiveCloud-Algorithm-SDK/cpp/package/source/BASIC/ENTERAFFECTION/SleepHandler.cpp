#include "SleepHandler.h"
#include "ModelAlgorithm.h"
#include "MathTool.h"
#include "AiNormParams.h"
#include "SVM.h"
#include "AiSleepModel4.h"

namespace basic
{
    namespace affection
    {
        namespace handler
        {
            static int debugIndex = 0;
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
                nc::NdArray<double> eegFeature, dsp::EEGPower &eegPower,
                int eegQuality, double timeCum, SleepHandlerTemp &tmp,  int smoothScope, int adjScope)
            {

                nc::NdArray<double> ncBetaNorm = {eegPower.betaNorm()};
                nc::NdArray<double> ncThetaNorm = {eegPower.thetaNorm()};
                nc::NdArray<double> ncDeltaNorm = {eegPower.deltaNorm()};
                nc::NdArray<double> ncGammaNorm = {eegPower.gammaNorm()};
                double betaAvg, thetaAvg, deltaAvg, gammaAvg;
                if (tmp.betaStore.size() < size_t(smoothScope))
                {
                    if (eegPower.betaNorm() > 0 && eegPower.thetaNorm() > 0 && eegPower.deltaNorm() > 0 && eegPower.gammaNorm() > 0)
                    {

                        tmp.betaStore = nc::hstack({tmp.betaStore, ncBetaNorm});

                        tmp.thetaStore = nc::hstack({tmp.thetaStore, ncThetaNorm});

                        tmp.deltaStore = nc::hstack({tmp.deltaStore, ncDeltaNorm});

                        tmp.gammaStore = nc::hstack({tmp.gammaStore, ncGammaNorm});
                    }
                    betaAvg = 0;
                    thetaAvg = 0;
                    deltaAvg = 0;
                    gammaAvg = 0;
                }
                else
                {
                    if (eegPower.betaNorm() > 0 && eegPower.thetaNorm() > 0 && eegPower.deltaNorm() > 0 && eegPower.gammaNorm() > 0)
                    {

                        tmp.betaStore = nc::hstack({tmp.betaStore[nc::Slice(1, tmp.betaStore.size())], ncBetaNorm});

                        tmp.thetaStore = nc::hstack({tmp.thetaStore[nc::Slice(1, tmp.thetaStore.size())], ncThetaNorm});

                        tmp.deltaStore = nc::hstack({tmp.deltaStore[nc::Slice(1, tmp.deltaStore.size())], ncDeltaNorm});

                        tmp.gammaStore = nc::hstack({tmp.gammaStore[nc::Slice(1, tmp.gammaStore.size())], ncGammaNorm});
                    }
                    betaAvg = nc::mean(tmp.betaStore).item();
                    thetaAvg = nc::mean(tmp.thetaStore).item();
                    deltaAvg = nc::mean(tmp.deltaStore).item();
                    gammaAvg = nc::mean(tmp.gammaStore).item();
                }
                // 特征处理
                eegFeature = nc::reshape(eegFeature, nc::Shape(1, -1));

                // 归一化
                auto sleepSVMNorm = params::sleepSVMNormParam.transpose();
                auto eegFeatureNormNc = mathtool::featureNorm(eegFeature, sleepSVMNorm, "sym");
                auto eegFeatureNorm = eegFeatureNormNc.toStlVector();

                double modelResArray[5];
                std::fill_n(modelResArray, 5, 0.0);
                
                if (tmp.model == nullptr)
                    tmp.model = svm_load_model(model::sleep_model_4);

                svm_node nodes[eegFeatureNorm.size()+1];
                for (int i = 0; i < eegFeatureNorm.size(); i++)
                {
                    nodes[i].index = i+1;
                    nodes[i].value = eegFeatureNorm[i];
                }
                nodes[eegFeatureNorm.size()].index = -1;

                auto modelSelect = svm_predict_probability(tmp.model, nodes, modelResArray);
              
                vectord modelRes = {modelResArray[4], modelResArray[0], modelResArray[3], modelResArray[2], modelResArray[1]};
                logValue(modelSelect, "modelSelect");
                logArray(modelRes, "modelResArray");

                auto curSleepPhase = model::sleepPhaseCal(modelRes);
                auto curSleepDegree = model::sleepDegreeCal(betaAvg, thetaAvg, deltaAvg, gammaAvg, modelRes, tmp.sleepFlag);
                // 信号质量校验
                double sleepProbThr = std::numeric_limits<double>::max();
                tmp.eegQualityStore = nc::hstack(
                    {tmp.eegQualityStore[nc::Slice(1, tmp.eegQualityStore.size())], {eegQuality}});
                auto minQuality = nc::min(tmp.eegQualityStore).item();
                if (minQuality > 1)
                    sleepProbThr = 3.5;
                else if (minQuality == 1)
                    sleepProbThr = 5.25;

                // 睡眠程度计算
                double sleepDegree = 0;
                if (tmp.sleepDegreeStore.size() < size_t(smoothScope))
                {
                    if (curSleepDegree >= 0)
                        tmp.sleepDegreeStore = nc::hstack({tmp.sleepDegreeStore, {curSleepDegree}});
                    sleepDegree = 0;
                }
                else
                {
                    if (curSleepDegree >= 0)
                        tmp.sleepDegreeStore = nc::hstack({tmp.sleepDegreeStore[nc::Slice(1, tmp.sleepDegreeStore.size())], {curSleepDegree}});
                    sleepDegree = nc::mean(tmp.sleepDegreeStore).item();
                }
                // 睡眠状态判断
                tmp.sleepPhaseStore = nc::hstack(
                    {tmp.sleepPhaseStore[nc::Slice(1, tmp.sleepPhaseStore.size())], {static_cast<int>(curSleepPhase)}});
                auto modelResSum = modelRes[2] + modelRes[3];
                tmp.sleepProbStore = nc::hstack(
                    {tmp.sleepProbStore[nc::Slice(1, tmp.sleepProbStore.size())], {modelResSum}});
                //---入睡判断
                int phaseAsleepLen = 0;
                for (auto &e : tmp.sleepPhaseStore)
                {
                    if (e == define::SleepPhaseEnum::ASLEEP)
                        phaseAsleepLen++;
                }
                bool isSleepProbSufficient = (phaseAsleepLen >= 15 && nc::sum(tmp.sleepProbStore).item() > sleepProbThr);
                bool isSleepStateSufficient = (tmp.sleepStateLen >= 70 && sleepDegree < 65);
                // 此处原来是200，改为40, 改动造成的影响未知
                bool isAwakeAndTimeSufficient = (tmp.sleepState == define::SleepStateEnum::AWAKES && timeCum > 200);

                if (isSleepProbSufficient || (isSleepStateSufficient && isAwakeAndTimeSufficient))
                {
                    tmp.sleepFlag = true;
                    tmp.sleepState = define::SleepStateEnum::ASLEEPS;
                    tmp.latencyTime = timeCum;
                }
                //---清醒恢复
                int phaseAwakeLen = 0;
                for (auto &e : tmp.sleepPhaseStore)
                {
                    if (e == define::SleepPhaseEnum::AWAKE)
                        phaseAwakeLen++;
                }
                if (phaseAwakeLen >= 5 && tmp.sleepState == define::SleepStateEnum::ASLEEPS)
                {
                    tmp.sleepState = define::SleepStateEnum::AWAKES;
                    tmp.sleepPhaseStore = nc::ones<int>(1, 20);
                    tmp.sleepProbStore = nc::zeros<double>(1, 7);
                }

                //---睡眠状态
                define::SleepStateEnum sleepState;
                if (tmp.sleepFlag)
                    sleepState = define::SleepStateEnum::ASLEEPS;
                else
                    sleepState = define::SleepStateEnum::AWAKES;

                // 睡眠分期计算
                auto curSleepStage = model::sleepStageCal(modelRes);
                
                tmp.sleepStageStore = nc::hstack({tmp.sleepStageStore[nc::Slice(1, tmp.sleepStageStore.size())], {curSleepStage}});
                logArray(tmp.sleepStageStore.toStlVector(), "tmp.sleepStageStore");
                auto sleepStage = define::SleepStage::WAKE;
                // 此处原来是200，改为40, 改动造成的影响未知
                if (timeCum >= 200)
                {
                    size_t stageWakeCount = 0;
                    size_t stageNrem1Count = 0;
                    size_t stageNrem2Count = 0;
                    size_t stageNrem3Count = 0;
                    size_t stageRemCount = 0;
                    
                    for (auto e : tmp.sleepStageStore)
                    {
                        switch (e)
                        {
                        case define::SleepStage::WAKE:
                            stageWakeCount++;
                            break;
                        case define::SleepStage::NREM1:
                            stageNrem1Count++;
                            break;
                        case define::SleepStage::NREM2:
                            stageNrem2Count++;
                            break;
                        case define::SleepStage::NREM3:
                            stageNrem3Count++;
                            break;
                        case define::SleepStage::REM:
                            stageRemCount++;
                            break;
                        }
                    }
                    if (stageWakeCount >= 3)
                        sleepStage = define::SleepStage::WAKE;
                    else if (stageNrem1Count >= 3)
                        sleepStage = define::SleepStage::NREM1;
                    else if (stageNrem2Count >= 3)
                        sleepStage = define::SleepStage::NREM2;
                    else if (stageNrem3Count >= 3)
                        sleepStage = define::SleepStage::NREM3;
                    else if (stageRemCount >= 3)
                        sleepStage = define::SleepStage::REM;
                    else
                        sleepStage = static_cast<define::SleepStage>(tmp.sleepStage);
                }
                tmp.sleepStage = sleepStage;

                if (sleepStage < 4 && sleepStage > 0)
                    tmp.sleepStateLen += 1;
                else if (sleepStage == 0)
                    tmp.sleepStateLen = 0;

                debugIndex += 1;
                SleepHandlerResult sleepResult;
                sleepResult.sleepDegree = sleepDegree;
                sleepResult.sleepState = sleepState;
                sleepResult.sleepStage = sleepStage;
                return sleepResult;
            }
        }
    }
}