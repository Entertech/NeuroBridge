#include "SimpleHandler.h"

namespace basic
{
    namespace affection
    {
        namespace handler
        {
            double attentionHandler(double betaNorm, double thetaNorm, double gammaNorm, double alphaNorm,
                                    AttentionHandlerTemp &tmp, int smoothScope, int adjScope,
                                    int adjPeriod, double offsetRatio, double initialVal)
            {
                // 数据质量判断
                bool dataValid = false;
                double attention = 0;
                if (betaNorm > 0 && thetaNorm > 0 && gammaNorm > 0 && alphaNorm > 0)
                    dataValid = true;
                // 数据无效时的输出策略
                if (!dataValid)
                {
                    if (tmp.attentionStore.size() < static_cast<size_t>(smoothScope))
                        attention = 0;
                    else
                        attention = nc::mean(tmp.attentionStore).item();
                    return attention;
                }

                vectord features = {nc::log(betaNorm / thetaNorm), gammaNorm, alphaNorm};
                // 标尺自适应

                model::rulersCalibration(
                    tmp.featuresStore,
                    features,
                    tmp.rulers,
                    params::attentionRuler,
                    adjScope, adjPeriod,
                    offsetRatio);

                // 权值调整
                if (adjScope / 2.0 < double(tmp.featuresStore[0].size()) && tmp.featuresStore[0].size() <= size_t(adjScope))
                {
                    if (tmp.weight + 2. / adjScope <= 0.72)
                        tmp.weight += 2. / adjScope;
                }
                else if (tmp.featuresStore[0].size() > size_t(adjScope))
                {
                    if (tmp.weight < 1)
                    {
                        tmp.weight += 2. / adjScope;
                        tmp.weight = std::min(tmp.weight, 1.0);
                    }
                }

                // 当前值计算

                auto curAttention = model::attentionCal(features, tmp.rulers);

                attention = model::outputProcess(
                    curAttention,
                    tmp.attentionStore,
                    tmp.weight,
                    initialVal,
                    smoothScope);

                return attention;
            }

            double relaxationHandler(double alphaNorm, double gammaNorm, double eeglAlphaNorm, double eegrAlphaNorm,
                                     RelaxationHandlerTemp &tmp, int smoothScope, int adjScope,
                                     int adjPeriod, double offsetRatio, double initialVal)
            {
                // 数据质量判断
                bool dataValid = false;
                double relaxation = 0;
                if (alphaNorm > 0 && gammaNorm > 0)
                    dataValid = true;

                // 数据无效时的输出策略
                if (!dataValid)
                {
                    if (tmp.relaxationStore.size() < static_cast<size_t>(smoothScope))
                        relaxation = 0;
                    else
                        relaxation = nc::mean(tmp.relaxationStore).item();
                    return relaxation;
                }

                vectord features;
                if (eeglAlphaNorm > 0 && eegrAlphaNorm > 0) // 双通道脑电设备
                {
                    features.push_back(alphaNorm);
                    features.push_back(gammaNorm);
                    features.push_back(nc::log(eegrAlphaNorm) - nc::log(eeglAlphaNorm));
                }
                else // 单通道脑电设备
                {
                    features.push_back(alphaNorm);
                    features.push_back(gammaNorm);
                    features.push_back(0);
                }

                // 标尺自适应

                model::rulersCalibration(
                    tmp.featuresStore,
                    features,
                    tmp.rulers,
                    params::relaxationRuler,
                    adjScope, adjPeriod,
                    offsetRatio);

                // 权值调整
                if (adjScope / 2.0 < double(tmp.featuresStore[0].size()) && tmp.featuresStore[0].size() <= size_t(adjScope))
                {
                    if (tmp.weight + 2. / adjScope <= 0.72)
                        tmp.weight += 2. / adjScope;
                }
                else if (tmp.featuresStore[0].size() > size_t(adjScope))
                {
                    if (tmp.weight < 1)
                    {
                        tmp.weight += 2. / adjScope;
                        tmp.weight = std::min(tmp.weight, 1.0);
                    }
                }

                // 当前值计算
                double curRelaxation = 0;
                if (eeglAlphaNorm > 0 && eegrAlphaNorm > 0)
                {
                    curRelaxation = model::relaxationCal(features, tmp.rulers, false);
                }
                else
                {
                    curRelaxation = model::relaxationCal(features, tmp.rulers, true);
                }

                relaxation = model::outputProcess(
                    curRelaxation,
                    tmp.relaxationStore,
                    tmp.weight,
                    initialVal,
                    smoothScope);

                return relaxation;
            }

            /// @brief 冥想度处理器
            /// @param alphaNorm α波能量标称值
            /// @param thetaNorm θ波能量标称值
            /// @param gammaNorm γ波能量标称值
            /// @param eeglAlphaNorm 左通道α波能量标称值
            /// @param eegrAlphaNorm 右通道α波能量标称值
            /// @param tmp 缓存
            /// @param smoothScope 冥想度值平滑范围
            /// @param adjScope 标尺修正范围（修正阶段长度）
            /// @param adjPeriod 标尺修正周期（每次修正间隔）
            /// @param offsetRatio 标尺偏置系数（取值范围：0~1，取零表示不使用标尺修正）
            /// @param initialVal 冥想度初始值
            /// @param stretchRatio 缩放尺度系数
            /// @return
            double meditationHandler(double alphaNorm, double thetaNorm, double gammaNorm, double eeglAlphaNorm, double eegrAlphaNorm,
                                     MeditationHandlerTemp &tmp, int smoothScope, int adjScope, int adjPeriod,
                                     double offsetRatio, double initialVal, double stretchRatio)
            {
                // 数据质量判断
                bool dataValid = false;
                double meditation = 0;
                if (alphaNorm > 0 && thetaNorm > 0 && gammaNorm > 0 && eeglAlphaNorm > 0 && eegrAlphaNorm > 0)
                    dataValid = true;

                // 数据无效时的输出策略
                if (!dataValid)
                {
                    if (tmp.meditationStore.size() < static_cast<size_t>(smoothScope))
                        meditation = 0;
                    else
                        meditation = nc::mean(tmp.meditationStore).item();
                    return meditation;
                }

                // 特征处理
                vectord features;
                if (eeglAlphaNorm > 0 && eegrAlphaNorm > 0) // 双通道脑电设备
                {
                    features.push_back(alphaNorm);
                    features.push_back(thetaNorm);
                    features.push_back(gammaNorm);
                    features.push_back(nc::log(eegrAlphaNorm) - nc::log(eeglAlphaNorm));
                }
                else // 单通道脑电设备
                {
                    features.push_back(alphaNorm);
                    features.push_back(thetaNorm);
                    features.push_back(gammaNorm);
                    features.push_back(0);
                }

                // 标尺自适应
                model::rulersCalibration(
                    tmp.featuresStore,
                    features,
                    tmp.rulers,
                    params::meditationRuler,
                    adjScope, adjPeriod,
                    offsetRatio);

                // 权值调整
                if (adjScope / 2.0 < double(tmp.featuresStore[0].size()) && tmp.featuresStore[0].size() <= size_t(adjScope))
                {
                    if (tmp.weight + 2. / adjScope <= 0.72)
                        tmp.weight += 2. / adjScope;
                }
                else if (tmp.featuresStore[0].size() > size_t(adjScope))
                {
                    if (tmp.weight < 1)
                    {
                        tmp.weight += 2. / adjScope;
                        tmp.weight = std::min(tmp.weight, 1.0);
                    }
                }
                // 当前值计算
                double curMeditation = 0;
                if (eeglAlphaNorm > 0 && eegrAlphaNorm > 0)
                {
                    vectord ratios = {1.9, 1.5, 1.2, 2.8};
                    vectord weights = {0.25, 0.5, -0.15, -0.1};
                    vectord bias = {0.0, 0.0, 15.0, 10.0};
                    curMeditation = model::meditationCal(features, tmp.rulers, ratios, weights, bias);
                }
                else
                {
                    vectord ratios = {1.9, 1.5, 1.2, 2.8};
                    vectord weights = {0.3, 0.55, -0.15, 0.};
                    vectord bias = {0.0, 0.0, 15.0, 0.0};
                    curMeditation = model::meditationCal(features, tmp.rulers, ratios, weights, bias);
                }

                meditation = model::outputProcess(
                    curMeditation,
                    tmp.meditationStore,
                    tmp.weight,
                    initialVal,
                    smoothScope);

                meditation = std::tanh((meditation - 50) / 50 * stretchRatio) * 50 / std::tanh(stretchRatio) + 50;
                return meditation;
            }

            double pressureHandler(double hrVal, double hrvVal, double hrLf, double freqRate,
                                   PressureHandlerTemp &tmp, int smoothScope)
            {
                bool dataValid = false;
                double pressure = 0;
                double freqRateAvg = 0;
                if (hrVal > 40 && hrvVal > 0 && freqRate > 0)
                    dataValid = true;
                // 数据无效时的输出策略
                if (!dataValid)
                {
                    if (tmp.pressureStore.size() < size_t(smoothScope))
                        pressure = 0;
                    else
                        pressure = nc::mean(tmp.pressureStore).item();
                    return pressure;
                }
                // 均值计算
                nc::NdArray<double> ncFreqRate = {freqRate};
                if (tmp.hrFreqRateStore.size() < size_t(smoothScope))
                {
                    tmp.hrFreqRateStore = nc::hstack({tmp.hrFreqRateStore, ncFreqRate});
                    freqRateAvg = 0;
                }
                else
                {
                    tmp.hrFreqRateStore = nc::hstack({tmp.hrFreqRateStore[nc::Slice(1, tmp.hrFreqRateStore.size())], ncFreqRate});
                    freqRateAvg = nc::mean(tmp.hrFreqRateStore).item();
                }
                // 当前值计算
                auto curPressure = model::pressureCal(hrVal, hrvVal, hrLf, freqRateAvg);
                nc::NdArray<double> ncCurPressure = {curPressure};
                // 缓存与输出值计算
                if (tmp.pressureStore.size() < size_t(smoothScope))
                {
                    if (curPressure >= 0)
                        tmp.pressureStore = nc::hstack({tmp.pressureStore, ncCurPressure});
                    pressure = 0;
                }
                else
                {
                    if (curPressure >= 0)
                        tmp.pressureStore = nc::hstack({tmp.pressureStore[nc::Slice(1, tmp.pressureStore.size())], ncCurPressure});
                    pressure = nc::mean(tmp.pressureStore).item();
                }

                return pressure;
            }

            double pleasureHandler(double eeglAlphaNorm, double eegrAlphaNorm, double eeglThetaNorm, double eegrThetaNorm,
                                   PleasureHandlerTemp &tmp, int smoothScope, int adjScope,
                                   int adjPeriod, double offsetRatio, double initialVal)
            {
                bool dataValid = false;
                double pleasure = 0.;

                // 数据质量判断
                if (eeglAlphaNorm > 0 && eegrAlphaNorm > 0 && eeglThetaNorm > 0 && eegrThetaNorm > 0)
                    dataValid = true;
                // 数据无效时的输出策略
                if (!dataValid)
                {
                    if (tmp.pleasureStore.size() < size_t(smoothScope))
                        pleasure = 0;
                    else
                        pleasure = nc::mean(tmp.pleasureStore).item();

                    return pleasure;
                }
                // 特征处理
                vectord features = {nc::log(eegrAlphaNorm) - nc::log(eeglAlphaNorm), nc::log(eegrThetaNorm) - nc::log(eeglThetaNorm)};

                // 标尺自适应
                model::rulersCalibration(
                    tmp.featuresStore,
                    features,
                    tmp.rulers,
                    params::pleasureRuler,
                    adjScope, adjPeriod,
                    offsetRatio);

                // 权值调整
                if (adjScope / 2. < double(tmp.featuresStore[0].size()) && tmp.featuresStore[0].size() <= size_t(adjScope))
                {
                    if (tmp.weight + 2. / adjScope <= 0.72)
                        tmp.weight += 2. / adjScope;
                }
                else if (tmp.featuresStore[0].size() > size_t(adjScope))
                {
                    if (tmp.weight < 1)
                    {
                        tmp.weight += 2. / adjScope;
                        tmp.weight = std::min(tmp.weight, 1.0);
                    }
                }

                // 当前值计算
                auto curPleasure = model::pleasureCal(features, tmp.rulers);
                pleasure = model::outputProcess(
                    curPleasure,
                    tmp.pleasureStore,
                    tmp.weight,
                    initialVal,
                    smoothScope);
                return pleasure;
            }

            double arousalHandler(double hrVal, double freqRate, ArousalHandlerTemp &tmp,
                                  int smoothScope, double initialVal)
            {
                bool dataValid = false;
                double arousal = 0;
                double freqRateAvg = 0;
                // 数据质量判断
                if (hrVal > 0 && freqRate > 0)
                    dataValid = true;

                // 数据无效时的输出策略
                if (!dataValid)
                {
                    if (tmp.arousalStore.size() < size_t(smoothScope))
                        arousal = 0;
                    else
                        arousal = nc::mean(tmp.arousalStore).item();
                    return arousal;
                }
                // 均值计算（暂存序列长度小于滑动窗半长时，不计算均值，小于滑动窗全长时，滑动窗不移动）
                nc::NdArray<double> ncFreqRate = {freqRate};
                if (tmp.hrFreqRateStore.size() < size_t(smoothScope))
                {
                    tmp.hrFreqRateStore = nc::hstack({tmp.hrFreqRateStore, ncFreqRate});
                    if (tmp.hrFreqRateStore.size() < size_t(smoothScope / 2))
                        freqRateAvg = 0;
                    else
                        freqRateAvg = nc::mean(tmp.hrFreqRateStore).item();
                }
                else
                {
                    tmp.hrFreqRateStore = nc::hstack({tmp.hrFreqRateStore[nc::Slice(1, tmp.hrFreqRateStore.size())], ncFreqRate});
                    freqRateAvg = nc::mean(tmp.hrFreqRateStore).item();
                }

                // 权值调整
                if (0 < tmp.arousalStore.size() && tmp.arousalStore.size() <= size_t(smoothScope / 2))
                {
                    if (tmp.weight + 0.4 / smoothScope < 0.72)
                        tmp.weight += 0.4 / smoothScope;
                }
                else if (tmp.arousalStore.size() > size_t(smoothScope / 2))
                {
                    if (tmp.weight < 1)
                    {
                        tmp.weight += 0.4 / smoothScope;
                        tmp.weight = std::min(tmp.weight, 1.0);
                    }
                }
                // 当前值计算
                auto curArousal = model::arousalCal(hrVal, freqRateAvg);

                // 缓存与输出值计算
                if (tmp.arousalStore.size() < size_t(smoothScope))
                {
                    if (curArousal >= 0)
                    {
                        curArousal = tmp.weight * curArousal + (1 - tmp.weight) * initialVal;
                        nc::NdArray<double> ncCurArousal = {curArousal};
                        tmp.arousalStore = nc::hstack({tmp.arousalStore, ncCurArousal});
                    }
                    arousal = 0;
                }
                else
                {
                    if (curArousal >= 0)
                    {
                        curArousal = tmp.weight * curArousal + (1 - tmp.weight) * initialVal;
                        nc::NdArray<double> ncCurArousal = {curArousal};
                        tmp.arousalStore = nc::hstack({tmp.arousalStore[nc::Slice(1, tmp.arousalStore.size())], ncCurArousal});
                    }
                    arousal = nc::mean(tmp.arousalStore).item();
                }
                return arousal;
            }

            double coherenceHandler(double syncCor, CoherenceHandlerTemp &tmp, int smoothScope)
            {
                bool dataValid = false;
                double coherence = 0;
                if (syncCor > 0)
                    dataValid = true;

                if (!dataValid)
                {
                    if (tmp.coherenceStore.size() < size_t(smoothScope))
                        coherence = 0;
                    else
                        coherence = nc::mean(tmp.coherenceStore).item();
                    return coherence;
                }

                // 当前值计算
                auto curCoherence = model::coherenceCal(syncCor);

                nc::NdArray<double> ncCurCoherence = {curCoherence};

                // 缓存与输出值计算
                if (tmp.coherenceStore.size() < size_t(smoothScope))
                {
                    if (curCoherence >= 0)
                        tmp.coherenceStore = nc::hstack({tmp.coherenceStore, ncCurCoherence});
                    coherence = 0;
                }
                else
                {
                    if (curCoherence >= 0)
                        tmp.coherenceStore = nc::hstack({tmp.coherenceStore[nc::Slice(1, tmp.coherenceStore.size())], ncCurCoherence});
                    coherence = nc::mean(tmp.coherenceStore).item();
                }
                return coherence;
            }

        }
    }
}